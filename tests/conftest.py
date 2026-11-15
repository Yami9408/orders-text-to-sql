"""Shared fixtures. Tests never touch the generated data/ folder."""

import pytest


@pytest.fixture
def database_path(tmp_path):
    """Path to a throwaway SQLite database for one test."""
    return tmp_path / "orders.db"
