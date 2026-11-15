"""Request and response models of the API."""

from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

MAX_QUESTION_LENGTH = 500

Question = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH),
]


class AskRequest(BaseModel):
    question: Question = Field(examples=["¿Cuánto vendimos en España en marzo de 2026?"])


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class HealthResponse(BaseModel):
    status: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    sql: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
