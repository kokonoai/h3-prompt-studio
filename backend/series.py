"""Explicit, non-destructive series ordering across independent productions."""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path

from .projects import atomic_json, safe_id


def _label(value, what, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{what} must be non-empty text under {limit} characters.")
    return value.strip()


class SeriesManager:
    def __init__(self, data_dir, load_production):
        self.directory = (Path(data_dir).resolve() / "series").resolve()
        self.archive = (Path(data_dir).resolve() / "series_archive").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)
        self.load_production = load_production

    def _path(self, ident):
        return self.directory / (safe_id(ident) + ".json")

    def validate(self, value, *, check_productions=False):
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("Unsupported series record.")
        result = copy.deepcopy(value)
        result["id"] = safe_id(result.get("id"))
        result["title"] = _label(result.get("title"), "Series title")
        collection_id = result.get("card_collection_id")
        result["card_collection_id"] = safe_id(collection_id) if collection_id else None
        episodes = result.get("episodes")
        if not isinstance(episodes, list) or not 1 <= len(episodes) <= 100:
            raise ValueError("A series needs 1–100 episodes.")
        seen = set()
        total = 0
        clean = []
        for index, episode in enumerate(episodes, 1):
            if not isinstance(episode, dict):
                raise ValueError("Each episode must be an object.")
            parts = episode.get("production_ids", [])
            if not isinstance(parts, list) or len(parts) > 30:
                raise ValueError("Each episode supports up to 30 ordered projects.")
            ids = []
            for ident in parts:
                ident = safe_id(ident)
                if ident in seen:
                    raise ValueError("A project can appear only once in one series.")
                if check_productions:
                    self.load_production(ident)
                seen.add(ident)
                ids.append(ident)
            total += len(ids)
            clean.append({"index": index,
                          "title": _label(episode.get("title") or f"Episode {index}", "Episode title"),
                          "production_ids": ids})
        if total > 100:
            raise ValueError("A series supports at most 100 project parts.")
        result["episodes"] = clean
        result["created_at"] = float(result.get("created_at", time.time()))
        result["updated_at"] = float(result.get("updated_at", time.time()))
        return result

    def get(self, ident):
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("Series not found.")
        return self.validate(json.loads(path.read_text(encoding="utf-8")))

    def list(self):
        values = []
        for path in self.directory.glob("*.json"):
            try:
                item = self.validate(json.loads(path.read_text(encoding="utf-8")))
                values.append({"id": item["id"], "title": item["title"],
                               "card_collection_id": item["card_collection_id"],
                               "episode_count": len(item["episodes"]),
                               "part_count": sum(len(ep["production_ids"]) for ep in item["episodes"]),
                               "updated_at": item["updated_at"]})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(values, key=lambda item: item["updated_at"], reverse=True)

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {"title", "episode_count", "card_collection_id"}:
            raise ValueError("Unsupported series fields.")
        count = body.get("episode_count", 10)
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("Choose 1–100 episodes.")
        now = time.time()
        value = self.validate({"schema_version": 1, "id": str(uuid.uuid4()),
            "title": body.get("title"), "card_collection_id": body.get("card_collection_id"),
            "episodes": [{"index": i, "title": f"Episode {i}", "production_ids": []}
                         for i in range(1, count + 1)],
            "created_at": now, "updated_at": now})
        atomic_json(self._path(value["id"]), value)
        return value

    def update(self, ident, body):
        if not isinstance(body, dict) or set(body) - {"title", "card_collection_id", "episodes"}:
            raise ValueError("Unsupported series update fields.")
        value = self.get(ident)
        value.update(copy.deepcopy(body))
        value["updated_at"] = time.time()
        value = self.validate(value, check_productions=True)
        atomic_json(self._path(value["id"]), value)
        return value

    def delete(self, ident):
        value = self.get(ident)
        path = self._path(value["id"])
        destination = self.archive / f"{value['id']}-{int(time.time())}.json"
        path.replace(destination)
        return {"deleted": True, "archived": True, "projects_preserved": True,
                "card_set_preserved": True, "videos_preserved": True}
