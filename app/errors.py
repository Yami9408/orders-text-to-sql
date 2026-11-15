"""Domain errors. Each has a stable ``code`` and a message that is safe to show to clients."""


class AppError(Exception):
    """Base class for expected failures that the API reports as structured errors."""

    code = "internal_error"
    default_message = "Unexpected error."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class LLMNotConfiguredError(AppError):
    code = "llm_not_configured"
    default_message = "The language model is not configured."


class LLMUnavailableError(AppError):
    code = "llm_unavailable"
    default_message = "The language model service is unavailable. Try again later."


class LLMTimeoutError(AppError):
    code = "llm_timeout"
    default_message = "The language model took too long to answer."


class LLMInvalidResponseError(AppError):
    code = "llm_invalid_response"
    default_message = "The language model did not return a usable SQL query."


class LLMRefusedError(AppError):
    code = "llm_refused"
    default_message = "The language model declined to answer this question."


class SQLValidationError(AppError):
    """Raised when SQL is not a single, read-only query over the allowed tables."""

    code = "sql_rejected"
    default_message = "The generated SQL was rejected."


class QueryTimeoutError(AppError):
    code = "query_timeout"
    default_message = "The query took too long to run."


class QueryFailedError(AppError):
    code = "query_failed"
    default_message = "The query could not be executed."
