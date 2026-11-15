"""Turn natural-language questions into SQL with Claude.

The rest of the app depends only on the ``SQLGenerator`` protocol, so the provider can be
replaced or mocked without touching the query service or the API.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from sqlalchemy import Engine, text

from app.config import Settings
from app.database import ORDER_COLUMNS, ORDERS_DDL, ORDERS_TABLE, VIEWS, view_definitions
from app.domain import SALES_STATUS
from app.errors import (
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRefusedError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
EFFORT = "low"  # one short SQL query does not need deep reasoning
MAX_RETRIES = 1
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FENCE_PATTERN = re.compile(r"^```(?:sql)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class SQLGenerator(Protocol):
    def generate_sql(self, question: str) -> str:
        """Return one SQL query answering ``question``."""
        ...


@dataclass(frozen=True)
class PromptContext:
    """Facts about the data that the model needs to write correct filters."""

    countries: tuple[str, ...]
    products: tuple[str, ...]
    statuses: tuple[str, ...]
    first_date: str
    last_date: str

    @classmethod
    def from_engine(cls, engine: Engine) -> "PromptContext":
        """Read the stored values and the date range from the database."""

        def distinct(connection, column: str) -> tuple[str, ...]:
            sql = f"SELECT DISTINCT {column} FROM {ORDERS_TABLE} ORDER BY 1"
            return tuple(connection.execute(text(sql)).scalars())

        with engine.connect() as connection:
            first, last = connection.execute(
                text(f"SELECT MIN(order_date), MAX(order_date) FROM {ORDERS_TABLE}")
            ).one()
            return cls(
                countries=distinct(connection, "country"),
                products=distinct(connection, "product"),
                statuses=distinct(connection, "status"),
                first_date=first or "",
                last_date=last or "",
            )


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def build_system_prompt(context: PromptContext, views: list[str]) -> str:
    """Build the system prompt from the real schema, views and stored values."""
    return f"""You translate questions about sales orders into one SQLite query.

Schema:
{ORDERS_DDL}

Views (already created):
{";\n\n".join(views)}

Stored values (use them exactly, with this spelling and case):
- country: {_quoted(context.countries)}
- product: {_quoted(context.products)}
- status: {_quoted(context.statuses)}
- order_date ranges from {context.first_date} to {context.last_date}.

Rules:
- Write exactly one SQLite SELECT statement. WITH clauses are allowed.
- Use only these tables and views: {", ".join((ORDERS_TABLE, *VIEWS))}.
  Use only these orders columns: {", ".join(ORDER_COLUMNS)}. Never invent tables or columns.
- Never modify data or schema, and never use PRAGMA, ATTACH, transactions or other
  administrative commands, other databases or SQLite internal tables.
- Sales, revenue and amounts sold count only orders with status = '{SALES_STATUS}'.
- The question may be in any language. Translate names to the stored values above
  (for example España -> 'Spain', Alemania -> 'Germany', portátil -> 'Laptop').
- If a month is given without a year, use its most recent occurrence within the date range.
- Filter dates with ranges on order_date, for example
  order_date >= '2026-03-01' AND order_date < '2026-04-01'.
- Give every computed column a descriptive snake_case alias, such as total_sales.
- Reply with the SQL only: no Markdown fences, comments or explanations."""


def extract_sql(reply: str) -> str:
    """Return the SQL in ``reply``, removing one surrounding Markdown fence if present."""
    candidate = reply.strip()
    match = FENCE_PATTERN.match(candidate)
    if match:
        candidate = match.group(1).strip()
    if not candidate:
        raise LLMInvalidResponseError()
    return candidate


class ClaudeSQLGenerator:
    """``SQLGenerator`` backed by the Claude Messages API."""

    def __init__(self, settings: Settings, system_prompt: str, client: Any = None) -> None:
        self._settings = settings
        self._system_prompt = system_prompt
        self._client = client

    def _get_client(self) -> Any:
        if not self._settings.anthropic_api_key or not self._settings.anthropic_model:
            raise LLMNotConfiguredError()
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key,
                timeout=self._settings.llm_timeout_seconds,
                max_retries=MAX_RETRIES,
            )
        return self._client

    def _create_message(self, client: Any, question: str) -> Any:
        request = {
            "model": self._settings.anthropic_model,
            "max_tokens": MAX_TOKENS,
            "system": self._system_prompt,
            "messages": [{"role": "user", "content": question}],
            "output_config": {"effort": EFFORT},
        }
        if self._settings.anthropic_fallbacks:
            return client.beta.messages.create(
                **request,
                betas=[FALLBACK_BETA],
                fallbacks=self._settings.anthropic_fallbacks,
            )
        return client.messages.create(**request)

    def generate_sql(self, question: str) -> str:
        client = self._get_client()
        try:
            response = self._create_message(client, question)
        except anthropic.APITimeoutError as error:
            raise LLMTimeoutError() from error
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as error:
            logger.error("Claude rejected the configuration: %s", error)
            raise LLMNotConfiguredError() from error
        except anthropic.APIError as error:
            logger.error("Claude request failed: %s", error)
            raise LLMUnavailableError() from error

        if response.stop_reason == "refusal":
            raise LLMRefusedError()
        if response.stop_reason == "max_tokens":
            raise LLMInvalidResponseError()
        reply = "".join(block.text for block in response.content if block.type == "text")
        return extract_sql(reply)


def create_sql_generator(settings: Settings, engine: Engine) -> ClaudeSQLGenerator:
    """Build the Claude generator with a prompt describing the current database."""
    prompt = build_system_prompt(PromptContext.from_engine(engine), view_definitions())
    return ClaudeSQLGenerator(settings, prompt)
