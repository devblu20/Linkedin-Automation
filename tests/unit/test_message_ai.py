"""Backend transport validation without network calls or real API credentials."""

import json
from io import BytesIO
from urllib.error import URLError

import pytest

from linkedin_automation.infrastructure import message_ai


def test_ai_missing_configuration_fails_without_calling_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(message_ai.AIUnavailable, match="backend OPENROUTER_API_KEY"):
        message_ai.MessageAI().generate("draft", {})


@pytest.mark.parametrize("bad", ["unsupported label", "invalid_json", "refusal", "incomplete"])
def test_ai_rejects_invalid_classification(monkeypatch, bad):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-real")
    monkeypatch.setenv("MESSAGE_AI_MODEL", "test-model")
    text = json.dumps({"classification": "not-a-supported-label"})
    if bad == "invalid_json":
        text = "no JSON"
    body = {
        "status": "incomplete" if bad == "incomplete" else "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "refusal" if bad == "refusal" else "output_text", "text": text}
                ],
            }
        ],
    }
    opener = type(
        "Opener", (), {"open": lambda *args, **kwargs: BytesIO(json.dumps(body).encode())}
    )()
    monkeypatch.setattr(message_ai, "build_opener", lambda *args: opener)
    with pytest.raises(message_ai.AIUnavailable, match="invalid result"):
        message_ai.MessageAI().generate("classify", {"reply": "hello"})


def test_ai_does_not_store_or_redirect_secrets_and_validates_schema(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-real")
    monkeypatch.setenv("MESSAGE_AI_MODEL", "test-model")
    selection = {"fact": "company", "question": 1, "followup": 0}

    def open_request(request, **kwargs):
        assert request.full_url == "https://api.openai.com/v1/responses"
        body = json.loads(request.data)
        assert body["store"] is False
        assert "test-secret-not-real" not in json.dumps(body)
        assert body["text"]["format"]["strict"] is True
        return BytesIO(
            json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(selection)}],
                        }
                    ],
                }
            ).encode()
        )

    opener = type("Opener", (), {"open": staticmethod(open_request)})()
    monkeypatch.setattr(message_ai, "build_opener", lambda *args: opener)
    assert message_ai.MessageAI().generate("draft", {}) == selection


def test_ai_network_errors_do_not_expose_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-not-real")
    monkeypatch.setenv("MESSAGE_AI_MODEL", "test-model")

    def broken(*args, **kwargs):
        raise URLError("test-secret-not-real")

    opener = type("Opener", (), {"open": staticmethod(broken)})()
    monkeypatch.setattr(message_ai, "build_opener", lambda *args: opener)
    with pytest.raises(message_ai.AIUnavailable) as failure:
        message_ai.MessageAI().generate("classify", {})
    assert "test-secret" not in str(failure.value)


def test_openrouter_backend_uses_correct_schema_endpoint_and_no_data_collection(monkeypatch):
    monkeypatch.setenv("MESSAGE_AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-key")
    monkeypatch.setenv("MESSAGE_AI_MODEL", "google/gemini-2.5-flash")

    def open_request(request, **kwargs):
        assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
        body = json.loads(request.data)
        assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "test-router-key" not in json.dumps(body)
        return BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"classification": "Question"})},
                        }
                    ]
                }
            ).encode()
        )

    opener = type("Opener", (), {"open": staticmethod(open_request)})()
    monkeypatch.setattr(message_ai, "build_opener", lambda *args: opener)
    assert message_ai.MessageAI().generate("classify", {"reply": "What does it cost?"}) == {
        "classification": "Question"
    }


@pytest.mark.parametrize(
    "choices",
    [
        [],
        [{"finish_reason": "length"}],
        [{"finish_reason": "stop", "message": {"content": "not JSON"}}],
    ],
)
def test_openrouter_incomplete_or_invalid_results_fail_closed(monkeypatch, choices):
    monkeypatch.setenv("MESSAGE_AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router-key")
    monkeypatch.setenv("MESSAGE_AI_MODEL", "test-model")
    opener = type(
        "Opener", (), {"open": lambda *a, **k: BytesIO(json.dumps({"choices": choices}).encode())}
    )()
    monkeypatch.setattr(message_ai, "build_opener", lambda *args: opener)
    with pytest.raises(message_ai.AIUnavailable, match="invalid result"):
        message_ai.MessageAI().generate("classify", {})
