"""Tests for reading settings from the environment and the .env file."""

from app.config import DEFAULT_MODEL, load_settings


def test_blank_environment_variable_does_not_hide_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY= file-key\nMAX_QUERY_ROWS=7\n")

    settings = load_settings({"ANTHROPIC_API_KEY": ""}, env_file)

    assert settings.anthropic_api_key == "file-key"
    assert settings.max_query_rows == 7


def test_environment_wins_and_defaults_apply(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=file-key\n")

    settings = load_settings({"ANTHROPIC_API_KEY": "env-key"}, env_file)

    assert settings.anthropic_api_key == "env-key"
    assert settings.anthropic_model == DEFAULT_MODEL
    assert settings.anthropic_fallbacks is None
    assert settings.database_path.name == "orders.db"


def test_missing_env_file_is_fine(tmp_path):
    assert load_settings({}, tmp_path / "missing.env").anthropic_api_key is None
