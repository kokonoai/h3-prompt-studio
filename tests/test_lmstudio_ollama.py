from backend.lmstudio import LMStudioClient


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
