"""Answer a question: generate SQL, validate it, run it read-only and serialize the result."""

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from app.errors import QueryFailedError, QueryTimeoutError, SQLValidationError
from app.services.llm_service import SQLGenerator
from app.services.sql_validator import validate_sql

logger = logging.getLogger(__name__)

PROGRESS_CHECK_INSTRUCTIONS = 1000  # how often SQLite calls the timeout check


@dataclass(frozen=True)
class QueryResult:
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool


def _json_value(value: Any) -> Any:
    """SQLite returns int, float, str, bytes or None; only bytes need converting."""
    return value.hex() if isinstance(value, bytes) else value


class QueryService:
    """Coordinates generation, validation and execution. Holds no per-request state."""

    def __init__(
        self,
        generator: SQLGenerator,
        engine: Engine,
        max_rows: int,
        timeout_seconds: float,
    ) -> None:
        self._generator = generator
        self._engine = engine
        self._max_rows = max_rows
        self._timeout_seconds = timeout_seconds

    def answer(self, question: str) -> QueryResult:
        sql = self._generator.generate_sql(question)
        try:
            safe_sql = validate_sql(sql)
        except SQLValidationError as error:
            logger.warning("Rejected generated SQL (%s): %s", error.message, sql)
            raise SQLValidationError(error.message, sql=sql) from error
        columns, rows, truncated = self._execute(safe_sql)
        return QueryResult(question, safe_sql, columns, rows, truncated)

    def _execute(self, sql: str) -> tuple[list[str], list[dict[str, Any]], bool]:
        """Run validated SQL; return at most ``max_rows`` rows and whether more existed."""
        deadline = time.monotonic() + self._timeout_seconds
        timed_out = False

        def past_deadline() -> int:
            nonlocal timed_out
            timed_out = time.monotonic() > deadline
            return int(timed_out)  # non-zero interrupts the query

        try:
            with self._engine.connect() as connection:
                driver = connection.connection.driver_connection
                driver.set_progress_handler(past_deadline, PROGRESS_CHECK_INSTRUCTIONS)
                try:
                    result = connection.exec_driver_sql(sql)
                    columns = list(result.keys())
                    fetched = result.fetchmany(self._max_rows + 1)
                finally:
                    driver.set_progress_handler(None, 0)
        except DBAPIError as error:
            if timed_out:
                raise QueryTimeoutError() from error
            logger.error("Query failed: %s | SQL: %s", error.orig, sql)
            raise QueryFailedError() from error

        rows = [
            {column: _json_value(value) for column, value in zip(columns, row, strict=True)}
            for row in fetched[: self._max_rows]
        ]
        return columns, rows, len(fetched) > self._max_rows
