# orders-text-to-sql — Implementation Spec

## 0. Context

This is my plan for a technical assessment. The brief asks me for:

1. A CSV of at least 1,000 orders (date, customer, country, product, amount, status) with deliberate errors (duplicates, malformed dates, blank amounts), and a script that cleans it and stores it in SQLite.
2. Two SQL views: sales by month and country, and the top 10 customers.
3. A FastAPI endpoint that takes a natural-language question (e.g. "¿cuánto vendimos en España en marzo?"), uses an LLM to turn it into SQL, validates it (read-only, allowed tables only) before running it, and returns the result, with a clear error when the LLM fails or returns something invalid.
4. At least 3 pytest tests: cleaning, query validation, and the endpoint with a mocked LLM.
5. A README: how to run it, the decisions I took, what I would do with more time, and how I used AI.

No API key in the repo. Incremental commits; a single commit is not accepted. I have 48 hours.

The reviewer reads my commit history to see how I think, and I will defend the code in a 30-minute interview, so I value clarity over feature count.

## 1. How I work

- I check the repository before changing anything and keep what is already there.
- I build one milestone at a time (section 13) and review each one (the changes, the commands run with their real output, the decisions) before moving to the next.
- Before every commit I run `pytest -q`, `ruff check .` and `ruff format --check .`, and I commit only when all three pass. I never commit failing or unverified code.
- Commits are authored by me (Yamile Reynoso Diaz) and use exactly the messages in section 13, with no co-author or tool trailers.
- If a later milestone reveals a bug in an earlier one, I fix it in its own commit with a message explaining why. I don't rewrite history.
- I never fabricate test results, logs or behaviour, and never commit `.env`, API keys, `data/*.csv` or `data/*.db`.
- I keep it simple: no Docker, LangChain, Celery, Redis, frontend or extra infrastructure.

## 2. Stack

Python 3.12, FastAPI, Pydantic v2, pandas, SQLite through SQLAlchemy, SQLGlot, the Anthropic Python SDK (`anthropic`) for Claude, pytest + httpx, Ruff, python-dotenv. Pin exact versions in `requirements.txt`. Ruff configured in `pyproject.toml` (target py312, line length 100, rules `E,F,I,B,UP`).

## 3. Repository structure

```
orders-text-to-sql/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, thin endpoints, error handlers
│   ├── config.py            # Settings from environment
│   ├── database.py          # Engines (read-write for loading, read-only for queries)
│   ├── schemas.py           # Pydantic request/response/error models
│   └── services/
│       ├── __init__.py
│       ├── llm_service.py   # SQLGenerator protocol + Claude implementation + prompt
│       ├── sql_validator.py # SQLGlot-based validation
│       └── query_service.py # generate → validate → execute → serialize
├── scripts/
│   ├── __init__.py
│   ├── generate_data.py
│   └── clean_and_load.py
├── sql/views.sql
├── tests/
│   ├── conftest.py
│   ├── test_cleaner.py
│   ├── test_views.py
│   ├── test_sql_validator.py
│   ├── test_llm_service.py
│   └── test_api.py
├── data/.gitkeep
├── .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 4. Synthetic dataset — `python -m scripts.generate_data`

Writes `data/orders.csv` with columns `order_id,date,customer,country,product,amount,status`.

- 1,200 base orders, `random.Random(42)`, ids `ORD-00001`…`ORD-01200`.
- 50 customers (`Customer 01`…`Customer 50`).
- Countries: Spain, Mexico, Germany, France, United States, Italy. Products: Laptop, Smartphone, Tablet, Monitor, Keyboard, Headphones, each with a plausible price range.
- Statuses weighted ~75% completed, 15% pending, 10% cancelled.
- Dates from 2024-01-01 to 2026-10-31 (so "March" without a year really is ambiguous).
- Most dates in `YYYY-MM-DD`; a deterministic 5% in `DD/MM/YYYY` (valid, must be normalized).
- Some valid rows get padding whitespace and mixed-case status (`" Completed "`), which cleaning must normalize, not reject.

Deliberate anomalies, with exact counts defined as constants at the top of the script and printed at the end:

| Anomaly | Count | Examples |
|---|---|---|
| Exact duplicate rows | 15 | full copy of an earlier row |
| Same `order_id`, different content | 5 | conflicting duplicate |
| Malformed dates | 15 | `2026-13-05`, `31/02/2026`, `March 5 2026`, `2026/03/15` |
| Blank amounts | 20 | `""` |
| Non-numeric amounts | 5 | `N/A`, `12,5O`, `abc` |
| Negative amounts | 3 | `-120.00` |
| Missing required fields | 5 | blank customer or country |
| Rows with two problems | 3 | bad date and blank amount |

Duplicates are inserted after their originals. Running the script twice yields a byte-identical file.

## 5. Cleaning and loading — `python -m scripts.clean_and_load`

Implement the cleaning as a pure function `clean_orders(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]` (valid, rejected), separate from file and DB I/O, so the tests can call it directly. Read the CSV with `dtype=str, keep_default_na=False`.

Rules, applied to every row (a row collects **all** its reasons):

1. Missing required columns → fail fast with a clear error (no partial output).
2. Strip whitespace from every field; lowercase `status`.
3. Empty `order_id`, `customer`, `country`, `product` → `missing_<field>`.
4. Date: accept exactly `YYYY-MM-DD` and `DD/MM/YYYY`, parsed strictly as real calendar dates; normalize to `YYYY-MM-DD`. Blank → `missing_date`; anything else → `invalid_date`. No guessing of other formats.
5. Amount: blank → `missing_amount`; not a decimal number with `.` as separator → `invalid_amount`; `<= 0` → `non_positive_amount`. Round to 2 decimals.
6. Status not in {completed, pending, cancelled} → `invalid_status`.
7. Deduplication, after the rules above: for each `order_id`, keep the **first row in file order that passed every rule**; every other row with that id is rejected with `duplicate_order_id`. Nothing is overwritten.

Outputs:

- `data/orders_cleaned.csv`: valid, normalized rows.
- `data/rejected_records.csv`: original raw values + `source_row` (1-based line in the input file) + `rejection_reasons` (semicolon-separated).
- `data/orders.db`: the `orders` table is dropped and recreated on every run (idempotent), valid rows inserted, views (re)created from `sql/views.sql`.

Schema:

```sql
CREATE TABLE orders (
    order_id   TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,  -- ISO YYYY-MM-DD
    customer   TEXT NOT NULL,
    country    TEXT NOT NULL,
    product    TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL
);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_country ON orders(country);
CREATE INDEX idx_orders_customer ON orders(customer);
```

Printed summary: input rows, valid rows loaded, rejected rows, then a count per reason, with the note "a record can have more than one reason, so per-reason counts may exceed the rejected total". The numbers must come from the run itself, never from hardcoded values.

## 6. SQL views — `sql/views.sql`

Business rule, used identically in views, prompt and README: **sales only count completed orders**.

- `monthly_sales_by_country(month, country, total_sales, total_orders)`: `strftime('%Y-%m', order_date)`, completed orders only, `ROUND(SUM(amount), 2)`, ordered by month and country.
- `top_customers(customer, total_orders, total_sales)`: completed orders only, `ORDER BY total_sales DESC, customer ASC LIMIT 10`.

Use `DROP VIEW IF EXISTS` + `CREATE VIEW`.

## 7. API

### `GET /health`
Runs `SELECT 1` on the read-only connection. `200 {"status": "ok"}`, or `503 {"status": "unavailable"}` if the DB can't be reached.

### `POST /ask`
Request: `{"question": str}`, stripped, 1–500 characters (FastAPI's default 422 for invalid input).

Success response:
```json
{
  "question": "¿Cuánto vendimos en España en marzo de 2026?",
  "sql": "SELECT ROUND(SUM(amount), 2) AS total_sales FROM orders WHERE ...",
  "columns": ["total_sales"],
  "rows": [{"total_sales": 15250.5}],
  "row_count": 1,
  "truncated": false
}
```

Errors use one shape, `{"error": {"code": "...", "message": "..."}}`, with a safe message. Full details are logged server-side only (never exceptions, stack traces, keys or provider payloads in the response):

| Case | Status | code |
|---|---|---|
| No API key / model configured | 503 | `llm_not_configured` |
| Provider error | 502 | `llm_unavailable` |
| Provider timeout | 504 | `llm_timeout` |
| Empty or unusable model output | 502 | `llm_invalid_response` |
| Model declined the request (`stop_reason: refusal`) | 422 | `llm_refused` |
| Generated SQL rejected by validator | 422 | `sql_rejected` (message gives the reason, e.g. "table 'sqlite_master' is not allowed"; include the rejected SQL in a `sql` field) |
| Query execution timeout | 504 | `query_timeout` |
| Other DB error | 500 | `query_failed` |

Endpoints stay thin: they call `query_service` and map domain exceptions to responses.

## 8. LLM integration — `app/services/llm_service.py`

- `class SQLGenerator(Protocol): def generate_sql(self, question: str) -> str`. The Claude implementation (`ClaudeSQLGenerator`) is injected with FastAPI `Depends`, so tests override it and `query_service` never imports `anthropic`.
- Call: `anthropic.Anthropic(api_key=..., timeout=LLM_TIMEOUT_SECONDS, max_retries=1).messages.create(model=ANTHROPIC_MODEL, max_tokens=16000, system=<prompt>, messages=[{"role": "user", "content": question}], output_config={"effort": "low"})`. Default model `claude-opus-5-5`.
- Do **not** send `temperature`, `top_p`, `top_k`, `thinking` or an assistant prefill: current Claude models reject them with a 400. Thinking is adaptive by default; `effort: "low"` keeps it short and cheap for one SQL query.
- Read the answer by joining the `text` blocks of `response.content` (skip `thinking` blocks). Check `response.stop_reason` first: `"refusal"` → `LLMRefused`; `"max_tokens"` → `LLMInvalidResponse`.
- Server-side refusal fallback: send `betas=["server-side-fallback-2026-07-01"]` with `fallbacks="default"` via `client.beta.messages.create`. Keep it optional behind a config flag (`ANTHROPIC_FALLBACKS=default`, empty to turn it off).
- System prompt includes:
  - the `orders` DDL and both views with their columns;
  - the **exact stored values** of country, product and status;
  - the data date range (min/max `order_date`, read from the DB when the generator is created);
  - the rules: SQLite dialect; exactly one `SELECT` (CTEs allowed); only `orders`, `monthly_sales_by_country`, `top_customers`; no data or schema changes, PRAGMA, ATTACH or other admin commands; sales = `status = 'completed'`; **translate names in any language to the stored value** (España → Spain, Alemania → Germany); **a month without a year means its most recent occurrence within the data range**; give result columns descriptive aliases; return only SQL, no Markdown or explanation.
- Post-processing: strip whitespace and remove one surrounding ```` ```sql ```` fence if present. Nothing else: never pull SQL out of prose. Empty result → `llm_invalid_response`.
- Map SDK exceptions most-specific first to domain exceptions: `anthropic.APITimeoutError` → `LLMTimeout`; `anthropic.AuthenticationError` → `LLMNotConfigured` (bad key, logged server-side); `anthropic.RateLimitError`, `anthropic.APIStatusError`, `anthropic.APIConnectionError` → `LLMUnavailable`. Missing key or model → `LLMNotConfigured` before any call.

## 9. SQL validation — `app/services/sql_validator.py`

`validate_sql(sql: str) -> str` returns the SQL unchanged or raises `SQLValidationError(reason)`. Rules:

1. Empty or whitespace → reject.
2. `sqlglot.parse(sql, dialect="sqlite")`; parse error → reject; more than one non-empty statement → reject (a trailing `;` is fine).
3. The root must be a `SELECT` or a set operation (`UNION`/`INTERSECT`/`EXCEPT`) of SELECTs, optionally with a `WITH` clause.
4. Walk the whole AST, subqueries and CTEs included: reject any `Insert`, `Update`, `Delete`, `Merge`, `Create`, `Drop`, `Alter`, `Command` (PRAGMA, ATTACH, DETACH, VACUUM, REINDEX…), `Transaction` or other non-query node.
5. Tables: gather the CTE names defined in the query. Every `exp.Table` that isn't a CTE name must be in the allowlist (case-insensitive) and must not carry a schema or database qualifier (`main.orders`, `other.orders` → reject). `sqlite_master`, `sqlite_schema`, `sqlite_temp_master` and any `sqlite_*` → reject.
6. Functions: reject `load_extension`, `readfile`, `writefile`, `edit`, `fts3_tokenizer`.

This is one of two layers; the read-only connection (section 10) is the other.

## 10. Execution — `app/services/query_service.py` + `app/database.py`

- A separate read-only engine for queries: `sqlite:///file:{path}?mode=ro&uri=true`, and `PRAGMA query_only = ON` on every connect.
- Timeout: `sqlite3` `set_progress_handler` aborts after `QUERY_TIMEOUT_SECONDS` → `query_timeout`.
- Row limit: `fetchmany(MAX_QUERY_ROWS + 1)`; return at most `MAX_QUERY_ROWS` rows and set `truncated: true` if there were more. The SQL itself is never rewritten.
- Values converted to JSON-safe types; `NULL` → `null`.
- Order: generate → validate → execute. Unvalidated SQL never reaches the connection.

## 11. Configuration

`.env.example` (no real values):

```
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-5-5
ANTHROPIC_FALLBACKS=default
DATABASE_PATH=data/orders.db
LLM_TIMEOUT_SECONDS=20
QUERY_TIMEOUT_SECONDS=5
MAX_QUERY_ROWS=100
```

Generation, cleaning, views and the whole test suite work without an API key. A missing key or model only affects `/ask` at request time (`llm_not_configured`), never app startup.

## 12. Tests

No real API key, no network, never `data/orders.db`: use `tmp_path` databases built with the same loading function from a small fixture DataFrame whose expected totals are known by hand. Tests are independent of execution order.

- `test_cleaner.py`: exact and conflicting duplicates (the first valid occurrence is kept); invalid dates rejected; `DD/MM/YYYY` normalized; blank, non-numeric and negative amounts rejected; whitespace and status normalized; multiple reasons recorded; missing column fails fast; same input → same output.
- `test_views.py`: monthly totals match the hand-computed fixture; pending/cancelled excluded; top customers ordered with the tie-break and limited to 10.
- `test_sql_validator.py`: accepts a plain SELECT, a view, a JOIN, a subquery, a CTE; rejects empty, invalid, multiple statements, INSERT/UPDATE/DELETE/DROP/CREATE/ALTER, PRAGMA, ATTACH, unknown table, `sqlite_master`, `main.orders`, a forbidden table hidden in a subquery or CTE, `load_extension`.
- `test_llm_service.py` (mocked `anthropic` client, no network): text blocks joined and thinking blocks ignored; fences stripped; empty output and `max_tokens` → `LLMInvalidResponse`; `refusal` → `LLMRefused`; timeout, auth, rate-limit and connection errors mapped; missing key → `LLMNotConfigured`; no `temperature`/`thinking` sent; prompt contains the business rule, allowed values and date range.
- `test_api.py` (fake generator via dependency override): success with correct results; `truncated` flag; empty and whitespace question → 422; each LLM error → its status/code; rejected SQL → 422 and **the DB is never called** (spy); DB error → 500 with no internals leaked; a write attempt can't change the DB even if it gets past the validator (test the read-only engine directly); `/health` OK.

Write tests together with each milestone, not all at the end.

## 13. Milestones

Each ends with green validation, my review and one commit with the message below. A fix to an earlier milestone gets its own `fix: ...` commit.

1. **Scaffold**: structure, `requirements.txt`, `pyproject.toml`, `.gitignore` (including `.DS_Store`), `.env.example`, this spec as `docs/SPEC.md`. → `chore: initialize project structure`
2. **Dataset generator**. → `feat: generate reproducible synthetic orders dataset`
3. **Cleaning + loading** + `test_cleaner.py`. → `feat: clean orders and load valid records into sqlite`
4. **Views** + `test_views.py`. → `feat: add monthly sales and top customers views`
5. **SQL validator** + `test_sql_validator.py`. → `feat: validate generated sql with sqlglot`
6. **Config + LLM service** + `test_llm_service.py`. → `feat: generate sql from natural language with llm`
7. **Query service + API** + `test_api.py`. → `feat: expose natural language query endpoint`
8. **README + final verification** (section 15). → `docs: add setup guide, design decisions and ai usage`

## 14. README (English, concise)

1. Overview (3–4 lines).
2. Quickstart: venv, install, generate, clean/load, run, test. Every command must have actually been run.
3. API usage: a working `curl` for `/ask` and `/health`, the error format.
4. Design decisions: date formats, rejection and dedup policy, completed-only rule, language/month-without-year rules, validator design, read-only connection, row-limit policy, the LLM interface.
5. Security: the two layers, what is blocked, and why errors are generic.
6. Limitations.
7. What I would do with more time.
8. AI usage: the tools I used (Claude, through Claude Code, for planning the spec, implementing and testing; Claude is also the LLM behind `/ask`), this spec (`docs/SPEC.md`) as my main prompt, what I accepted and what I corrected, and the time I spent. Only real facts: nothing invented.

## 15. Final verification

From a fresh venv:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.generate_data
python -m scripts.clean_and_load
pytest -q
ruff check . && ruff format --check .
uvicorn app.main:app
# GET /health, GET /docs, POST /ask
```

I test `/ask` with the real LLM only if my key is configured, and I say so in the README. Without a key, I check the `llm_not_configured` response and rely on the mocked tests. I check that `git status` is clean and that `git log` shows no secrets or data files.

Before sending the link I confirm: everything above ran for real, the commit list matches section 13, the README states whether a real LLM call was made, and the repo is pushed to GitHub.
