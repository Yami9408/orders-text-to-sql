"""Project paths and environment-based configuration."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODEL = "claude-opus-5-5"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


ENV_FILE = PROJECT_ROOT / ".env"


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


def load_settings(environ: Mapping[str, str], env_file: Path) -> Settings:
    """Build settings from ``environ`` and ``env_file``.

    A non-blank environment variable wins over the file. A blank one does not hide the
    file's value, so an empty ``ANTHROPIC_API_KEY`` exported by the shell is ignored.
    """
    file_values = dotenv_values(env_file) if env_file.is_file() else {}

    def read(name: str, default: str | None = None) -> str | None:
        for source in (environ, file_values):
            value = (source.get(name) or "").strip()
            if value:
                return value
        return default

    return Settings(
        database_path=_resolve_path(read("DATABASE_PATH", "data/orders.db")),
        anthropic_api_key=read("ANTHROPIC_API_KEY"),
        anthropic_model=read("ANTHROPIC_MODEL", DEFAULT_MODEL),
        anthropic_fallbacks=read("ANTHROPIC_FALLBACKS"),
        llm_timeout_seconds=float(read("LLM_TIMEOUT_SECONDS", "20")),
        query_timeout_seconds=float(read("QUERY_TIMEOUT_SECONDS", "5")),
        max_query_rows=int(read("MAX_QUERY_ROWS", "100")),
    )


@lru_cache
def get_settings() -> Settings:
    """Return the settings for this process, read once."""
    return load_settings(os.environ, ENV_FILE)
