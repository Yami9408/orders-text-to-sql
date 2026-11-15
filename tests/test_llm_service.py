"""Tests for the Claude SQL generator with a fake client (no network, no API key)."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.database import view_definitions
from app.errors import (
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRefusedError,
    LLMTimeoutError,
    LLMUnavailableError,
    UnsupportedQuestionError,
)
from app.services.llm_service import ClaudeSQLGenerator, PromptContext, build_system_prompt
from tests.factories import build_database, make_order

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def settings(**overrides) -> Settings:
    values = {"database_path": "unused.db", "anthropic_api_key": "test-key"} | overrides
    return Settings(**values)


def reply(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ]
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def generator(outcome, **overrides) -> tuple[ClaudeSQLGenerator, FakeMessages]:
    messages = FakeMessages(outcome)
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    return ClaudeSQLGenerator(settings(**overrides), "system prompt", client), messages


@pytest.mark.parametrize(
    "text",
    ["SELECT 1", "  SELECT 1\n", "```sql\nSELECT 1\n```", "```\nSELECT 1\n```"],
)
def test_sql_is_extracted_from_text_blocks(text):
    sql_generator, messages = generator(reply(text))

    assert sql_generator.generate_sql("question") == "SELECT 1"
    request = messages.calls[0]
    assert request["messages"] == [{"role": "user", "content": "question"}]
    assert request["system"] == "system prompt"
    assert not {"temperature", "top_p", "top_k", "thinking"} & request.keys()


def test_fallbacks_are_sent_only_when_configured():
    sql_generator, messages = generator(reply("SELECT 1"), anthropic_fallbacks="default")

    sql_generator.generate_sql("question")

    assert messages.calls[0]["fallbacks"] == "default"
    assert messages.calls[0]["betas"] == ["server-side-fallback-2026-07-01"]


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (reply("   "), LLMInvalidResponseError),
        (reply("SELECT", stop_reason="max_tokens"), LLMInvalidResponseError),
        (reply("", stop_reason="refusal"), LLMRefusedError),
        (anthropic.APITimeoutError(request=REQUEST), LLMTimeoutError),
        (anthropic.APIConnectionError(request=REQUEST), LLMUnavailableError),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=REQUEST), body=None
            ),
            LLMUnavailableError,
        ),
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx2.Response(401, request=REQUEST), body=None
            ),
            LLMNotConfiguredError,
        ),
    ],
)
def test_failures_become_domain_errors(outcome, error):
    sql_generator, _ = generator(outcome)

    with pytest.raises(error):
        sql_generator.generate_sql("question")


@pytest.mark.parametrize("text", ["CANNOT_ANSWER", " cannot_answer; ", "```\nCANNOT_ANSWER\n```"])
def test_cannot_answer_reply_is_reported(text):
    sql_generator, _ = generator(reply(text))

    with pytest.raises(UnsupportedQuestionError):
        sql_generator.generate_sql("Borra todos los pedidos")


@pytest.mark.parametrize("missing", ["anthropic_api_key", "anthropic_model"])
def test_missing_configuration_fails_before_calling_claude(missing):
    sql_generator, messages = generator(reply("SELECT 1"), **{missing: None})

    with pytest.raises(LLMNotConfiguredError):
        sql_generator.generate_sql("question")
    assert messages.calls == []


def test_prompt_describes_the_real_data(database_path):
    engine = build_database(
        database_path,
        make_order(order_id="1", date="2024-02-01", country="Spain", status="pending"),
        make_order(order_id="2", date="2026-10-31", country="Italy"),
    )

    prompt = build_system_prompt(PromptContext.from_engine(engine), view_definitions())

    assert "'Italy', 'Spain'" in prompt
    assert "from 2024-02-01 to 2026-10-31" in prompt
    assert "status = 'completed'" in prompt
    assert "CREATE VIEW top_customers" in prompt
    assert "DROP VIEW" not in prompt
    assert "reply with exactly CANNOT_ANSWER" in prompt
