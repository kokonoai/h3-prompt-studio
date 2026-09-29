import json
import threading
import time
from pathlib import Path

from backend.projects import atomic_json


def test_concurrent_atomic_json_writers_use_independent_temporary_files(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    original_write = Path.write_text
    counter_lock = threading.Lock()
    active_writes = 0
    max_active_writes = 0
    temporary_names = []

    def synchronized_write(path, *args, **kwargs):
        nonlocal active_writes, max_active_writes
        if path.parent == tmp_path and path.suffix == '.tmp':
            with counter_lock:
                temporary_names.append(path.name)
                active_writes += 1
                max_active_writes = max(max_active_writes, active_writes)
            time.sleep(0.05)
            try:
                return original_write(path, *args, **kwargs)
            finally:
                with counter_lock:
                    active_writes -= 1
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', synchronized_write)
    errors = []
    values = [{'writer': 1, 'payload': '甲' * 2000}, {'writer': 2, 'payload': '乙' * 2000}]

    def save(value):
        try:
            atomic_json(target, value)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(value,)) for value in values]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert max_active_writes == 1
    assert len(set(temporary_names)) == 2
    assert json.loads(target.read_text(encoding='utf-8')) in values
    assert not list(tmp_path.glob('*.tmp'))
