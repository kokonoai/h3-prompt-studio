import httpx

from backend import resources


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"queue_running": [], "queue_pending": []}


def test_comfy_queue_read_timeout_is_retried_without_assuming_idle(monkeypatch):
    manager = resources.ResourceManager(
        lambda: {"comfy_urls": ["http://127.0.0.1:8188"]}, lambda: None)
    attempts = []

    def get(_url, *, timeout, trust_env):
        attempts.append((timeout, trust_env))
        if len(attempts) < 3:
            raise httpx.ReadTimeout("temporarily slow")
        return _Response()

    monkeypatch.setattr(resources, "tcp_listener_ports", lambda: frozenset({8188}))
    monkeypatch.setattr(resources.httpx, "get", get)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    assert manager.queues() == [{"url": "http://127.0.0.1:8188", "online": True,
                                 "running": 0, "pending": 0}]
    assert attempts == [(15, False), (30, False), (60, False)]


def test_comfy_queue_repeated_timeout_remains_fail_closed(monkeypatch):
    manager = resources.ResourceManager(
        lambda: {"comfy_urls": ["http://127.0.0.1:8188"]}, lambda: None)
    attempts = []

    def get(_url, *, timeout, trust_env):
        attempts.append(timeout)
        raise httpx.ReadTimeout("still slow")

    monkeypatch.setattr(resources, "tcp_listener_ports", lambda: frozenset({8188}))
    monkeypatch.setattr(resources.httpx, "get", get)
    monkeypatch.setattr(resources.time, "sleep", lambda _seconds: None)

    try:
        manager.queues()
        assert False, "a repeatedly unresponsive queue must never be assumed idle"
    except resources.ResourceError as exc:
        assert "ReadTimeout" in str(exc)
    assert attempts == [15, 30, 60]


def test_adjacent_owned_ai_call_reuses_empty_handoff_despite_warm_kv_cache(monkeypatch):
    class Client:
        origin = "http://127.0.0.1:11434/v1"
        is_ollama = True

        def loaded_instances(self):
            return [{"id": "h3ps-assistant-owned", "model_key": "gemma4:31b"}]

    settings = {"ai_memory_mode": "exclusive", "comfy_urls": ["http://127.0.0.1:8188"],
                "lm_url": "http://127.0.0.1:11434/v1", "context_length": 32768}
    client = Client()
    manager = resources.ResourceManager(lambda: settings, lambda: client)
    manager.instance_id = manager.baseline_instance_id = "h3ps-assistant-owned"
    manager.model_key = "gemma4:31b"
    manager.instance_endpoint = "http://127.0.0.1:11434"
    manager.exclusive_ownership = {"endpoint": manager.instance_endpoint,
                                   "instance_id": manager.instance_id,
                                   "model_key": manager.model_key}
    manager.ai_idle_memory_mib = 18000
    manager.comfy_kind = "empty"
    monkeypatch.setattr(manager, "assert_idle", lambda: [
        {"url": "http://127.0.0.1:8188", "online": True, "running": 0, "pending": 0}])
    monkeypatch.setattr(resources, "gpu_snapshot", lambda: {
        "used_mib": 29000, "free_mib": 3000, "total_mib": 32000, "name": "GPU"})

    def unexpected_release(*_args, **_kwargs):
        raise AssertionError("an adjacent owned AI call must not release Comfy again")

    monkeypatch.setattr(resources.httpx, "post", unexpected_release)

    result = manager._prepare_ai("gemma4:31b")

    assert result["ready"] is True
    assert manager.stage == "AI ready"
