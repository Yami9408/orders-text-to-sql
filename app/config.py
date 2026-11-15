"""Project paths and environment-based configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    """Runtime settings, read once from the environment (and ``.env``)."""

    database_path: Path


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings, loading ``.env`` from the project root if present."""
    load_dotenv(PROJECT_ROOT / ".env")
    return Settings(
        database_path=_resolve_path(os.getenv("DATABASE_PATH", "data/orders.db")),
    )
