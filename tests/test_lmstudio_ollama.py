import io
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from backend.lmstudio import LMStudioClient, LMStudioError


SIMPLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}


def test_ollama_structured_completion_disables_thinking(monkeypatch):
    client = LMStudioClient("http://127.0.0.1:11434/v1")
    captured = {}
    monkeypatch.setattr(client, "_loaded_model", lambda model, has_images: (model, {"reasoning": {}}))

    def request(method, path, payload):
        captured.update(payload)
        return {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 5},
        }

    monkeypatch.setattr(client, "_request", request)
    assert client.complete_json("gemma4:31b", "Return JSON.", "Do the task.", SIMPLE_SCHEMA) == {"ok": True}
    assert captured["reasoning_effort"] == "none"


def test_lm_studio_without_off_capability_keeps_model_default(monkeypatch):
    client = LMStudioClient("http://127.0.0.1:1234/v1")
    captured = {}
    monkeypatch.setattr(client, "_loaded_model", lambda model, has_images: (model, {"reasoning": {}}))

    def request(method, path, payload):
        captured.update(payload)
        return {
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr(client, "_request", request)
    assert client.complete_json("local-model", "Return JSON.", "Do the task.", SIMPLE_SCHEMA) == {"ok": True}
    assert "reasoning_effort" not in captured


def test_ollama_http_error_names_provider_and_preserves_safe_reason():
    client = LMStudioClient("http://127.0.0.1:11434/v1")
    body = b'{"error":"runner process terminated unexpectedly"}'
    client._opener.open = Mock(side_effect=HTTPError(
        'http://127.0.0.1:11434/v1/chat/completions', 500, '', {}, io.BytesIO(body)))

    with pytest.raises(LMStudioError) as failure:
        client._request('POST', '/v1/chat/completions', {})

    assert failure.value.code == 'http_error'
    assert str(failure.value) == 'Ollama returned HTTP 500: runner process terminated unexpectedly'
    assert failure.value.detail == body.decode()
