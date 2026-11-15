"""FastAPI application: thin endpoints over the query service."""

import logging
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.database import create_read_only_engine
from app.errors import AppError, QueryFailedError
from app.schemas import AskRequest, AskResponse, ErrorDetail, ErrorResponse, HealthResponse
from app.services.llm_service import SQLGenerator, create_sql_generator
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, HTTPStatus] = {
    "llm_not_configured": HTTPStatus.SERVICE_UNAVAILABLE,
    "llm_unavailable": HTTPStatus.BAD_GATEWAY,
    "llm_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "llm_invalid_response": HTTPStatus.BAD_GATEWAY,
    "llm_refused": HTTPStatus.UNPROCESSABLE_ENTITY,
    "unsupported_question": HTTPStatus.UNPROCESSABLE_ENTITY,
    "sql_rejected": HTTPStatus.UNPROCESSABLE_ENTITY,
    "query_timeout": HTTPStatus.GATEWAY_TIMEOUT,
    "query_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
}
ERROR_RESPONSES = {int(status): {"model": ErrorResponse} for status in set(STATUS_BY_CODE.values())}

app = FastAPI(
    title="orders-text-to-sql",
    description="Ask questions about orders in natural language; answered with validated SQL.",
    version="0.1.0",
)


@lru_cache
def _read_only_engine(path: Path) -> Engine:
    return create_read_only_engine(path)


@lru_cache
def _sql_generator(settings: Settings, engine: Engine) -> SQLGenerator:
    try:
        return create_sql_generator(settings, engine)
    except SQLAlchemyError as error:
        logger.error("Could not read the database to build the prompt: %s", error)
        raise QueryFailedError("The database is unavailable.") from error


def get_engine(settings: Annotated[Settings, Depends(get_settings)]) -> Engine:
    return _read_only_engine(settings.database_path)


def get_sql_generator(
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> SQLGenerator:
    return _sql_generator(settings, engine)


def get_query_service(
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_engine)],
    generator: Annotated[SQLGenerator, Depends(get_sql_generator)],
) -> QueryService:
    return QueryService(
        generator,
        engine,
        max_rows=settings.max_query_rows,
        timeout_seconds=settings.query_timeout_seconds,
    )


def _error_response(status: HTTPStatus, detail: ErrorDetail) -> JSONResponse:
    body = ErrorResponse(error=detail).model_dump(exclude_none=True)
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(AppError)
def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
    status = STATUS_BY_CODE.get(error.code, HTTPStatus.INTERNAL_SERVER_ERROR)
    return _error_response(
        status, ErrorDetail(code=error.code, message=error.message, sql=error.sql)
    )


@app.exception_handler(Exception)
def handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
    logger.exception("Unexpected error", exc_info=error)
    fallback = AppError()
    return _error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR, ErrorDetail(code=fallback.code, message=fallback.message)
    )


@app.get("/health", response_model=HealthResponse)
def health(engine: Annotated[Engine, Depends(get_engine)]) -> JSONResponse:
    """Report whether the API is up and the database can be read."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        logger.error("Health check failed: %s", error)
        return JSONResponse(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE, content={"status": "unavailable"}
        )
    return JSONResponse(content={"status": "ok"})


@app.post("/ask", response_model=AskResponse, responses=ERROR_RESPONSES)
def ask(
    request: AskRequest,
    service: Annotated[QueryService, Depends(get_query_service)],
) -> AskResponse:
    """Answer a natural-language question about the orders."""
    result = service.answer(request.question)
    return AskResponse(
        question=result.question,
        sql=result.sql,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        truncated=result.truncated,
    )
