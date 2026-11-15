"""Project paths and environment-based configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODEL = "claude-opus-5-5"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _optional(name: str, default: str | None = None) -> str | None:
    """Return the variable stripped, or ``default`` when unset; empty means ``None``."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip() or None


@dataclass(frozen=True)
class Settings:
    """Runtime settings, read once from the environment (and ``.env``)."""

    database_path: Path
    anthropic_api_key: str | None = None
    anthropic_model: str | None = DEFAULT_MODEL
    anthropic_fallbacks: str | None = None
    llm_timeout_seconds: float = 20.0
    query_timeout_seconds: float = 5.0
    max_query_rows: int = 100


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings, loading ``.env`` from the project root if present."""
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        database_path=_resolve_path(os.getenv("DATABASE_PATH", "data/orders.db")),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        anthropic_model=_optional("ANTHROPIC_MODEL", DEFAULT_MODEL),
        anthropic_fallbacks=_optional("ANTHROPIC_FALLBACKS"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        query_timeout_seconds=float(os.getenv("QUERY_TIMEOUT_SECONDS", "5")),
        max_query_rows=int(os.getenv("MAX_QUERY_ROWS", "100")),
    )
