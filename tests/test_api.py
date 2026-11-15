"""API tests with a fake SQL generator and a throwaway database."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import Settings, get_settings
from app.database import create_read_only_engine
from app.errors import LLMRefusedError, LLMTimeoutError, LLMUnavailableError
from app.main import app, get_engine, get_sql_generator
from tests.factories import build_database, make_order

SPAIN_MARCH_SQL = (
    "SELECT ROUND(SUM(amount), 2) AS total_sales FROM orders WHERE country = 'Spain' "
    "AND order_date >= '2026-03-01' AND order_date < '2026-04-01' AND status = 'completed'"
)


class FakeGenerator:
    def __init__(self, outcome: str | Exception) -> None:
        self.outcome = outcome
        self.questions: list[str] = []

    def generate_sql(self, question: str) -> str:
        self.questions.append(question)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class SpyEngine:
    """Stands in for the database and records whether anything tried to use it."""

    used = False

    def connect(self):
        self.used = True
        raise AssertionError("the database must not be reached")


@pytest.fixture
def read_engine(database_path):
    build_database(
        database_path,
        make_order(order_id="1", date="2026-03-02", amount="100.10"),
        make_order(order_id="2", date="2026-03-20", amount="50.20"),
        make_order(order_id="3", date="2026-03-21", amount="999.00", status="pending"),
    )
    return create_read_only_engine(database_path)


@pytest.fixture
def client(read_engine, database_path):
    """Return ``ask(outcome, **settings)``: post a question with a fake generator."""

    def ask(outcome, question="¿Cuánto vendimos en España en marzo?", engine=None, **settings):
        generator = FakeGenerator(outcome)
        app.dependency_overrides[get_settings] = lambda: Settings(database_path, **settings)
        app.dependency_overrides[get_engine] = lambda: engine or read_engine
        app.dependency_overrides[get_sql_generator] = lambda: generator
        response = TestClient(app).post("/ask", json={"question": question})
        return response, generator

    yield ask
    app.dependency_overrides.clear()


def test_question_is_answered_with_validated_sql(client):
    response, generator = client(SPAIN_MARCH_SQL)

    assert response.status_code == 200
    assert response.json() == {
        "question": "¿Cuánto vendimos en España en marzo?",
        "sql": SPAIN_MARCH_SQL,
        "columns": ["total_sales"],
        "rows": [{"total_sales": 150.3}],
        "row_count": 1,
        "truncated": False,
    }
    assert generator.questions == ["¿Cuánto vendimos en España en marzo?"]


def test_rows_are_limited_and_nulls_kept(client):
    response, _ = client(
        "SELECT order_id, NULL AS note FROM orders ORDER BY order_id", max_query_rows=2
    )

    body = response.json()
    assert body["rows"] == [{"order_id": "1", "note": None}, {"order_id": "2", "note": None}]
    assert body["truncated"] is True


@pytest.mark.parametrize("question", ["", "   ", "x" * 501])
def test_invalid_questions_are_rejected_before_the_llm(client, question):
    response, generator = client("SELECT 1", question=question)

    assert response.status_code == 422
    assert generator.questions == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (LLMTimeoutError(), 504, "llm_timeout"),
        (LLMUnavailableError(), 502, "llm_unavailable"),
        (LLMRefusedError(), 422, "llm_refused"),
    ],
)
def test_llm_errors_are_reported_clearly(client, error, status, code):
    response, _ = client(error)

    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": error.message}}


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM orders", "SELECT * FROM sqlite_master", "SELECT 1; DROP TABLE orders"],
)
def test_unsafe_sql_never_reaches_the_database(client, sql):
    spy = SpyEngine()

    response, _ = client(sql, engine=spy)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sql_rejected"
    assert response.json()["error"]["sql"] == sql
    assert spy.used is False


def test_database_errors_are_generic(client):
    response, _ = client("SELECT missing_column FROM orders")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "query_failed", "message": "The query could not be executed."}
    }


def test_slow_queries_time_out(client):
    endless = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"
    )

    response, _ = client(endless, query_timeout_seconds=0.05)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "query_timeout"


def test_read_only_engine_cannot_write(read_engine):
    with read_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.exec_driver_sql("DELETE FROM orders")
        assert connection.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 3


def test_health_reports_database_state(read_engine, tmp_path):
    app.dependency_overrides[get_engine] = lambda: read_engine
    assert TestClient(app).get("/health").json() == {"status": "ok"}

    app.dependency_overrides[get_engine] = lambda: create_read_only_engine(tmp_path / "none.db")
    response = TestClient(app).get("/health")
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
