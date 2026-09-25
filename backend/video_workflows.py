"""A local library of user-imported, H3-compatible ComfyUI API workflows."""
from __future__ import annotations

import copy
import json
import math
import time
import uuid
from pathlib import Path

from .projects import atomic_json, safe_id


BUILTIN = {
    "id": "builtin", "name": "H3 Studio · built-in tested workflow",
    "description": "The unchanged measured H3/MMH3 workflow shipped with Prompt Studio.",
    "builtin": True, "modes": ["ref2va", "i2va", "fl2va", "l2va", "t2va"],
    "created_at": 0,
}
REF8_WORKFLOW_ID = "h3_ref8_lora_accel"
REF8_LORA = r"maxminih3\minimax_h3_turbo_4step_ckpt500_pruned_comfyui.safetensors"
REF8_BUILTIN = {
    "id": REF8_WORKFLOW_ID, "name": "8步 LoRA 加速",
    "description": "Attachment-derived Reference-to-Video recipe: 8 sampling steps, res_multistep, the supplied LoRA at 0.5, and video/audio shifts 12/6. The original H3 workflow is unchanged.",
    "builtin": True, "modes": ["ref2va"], "created_at": 0,
}
REF8_GRAPH_PATH = Path(__file__).resolve().parents[1] / "workflows" / "H3_Ref2VA_8Step_LoRA.api.json"


def ref8_recipe_settings():
    """Exact settings for the optional attachment recipe, never the default."""
    return {"quality": "lora8", "steps": 8,
            "loras": [{"name": REF8_LORA, "strength": 0.5, "enabled": True}],
            "shift_video": 12.0, "shift_audio": 6.0}


def _text(value, label, maximum, default=""):
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be text no longer than {maximum} characters.")
    return value.strip()


def _graph(value):
    if isinstance(value, str):
        if len(value) > 2_000_000:
            raise ValueError("The ComfyUI API workflow is too large.")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Choose a valid ComfyUI API workflow JSON file.") from exc
    # ComfyUI's queue/history exports commonly wrap the API graph in a
    # top-level ``prompt`` field. Accept that form as well as a bare API graph.
    if isinstance(value, dict) and isinstance(value.get("prompt"), dict):
        value = value["prompt"]
    if not isinstance(value, dict) or not value or len(value) > 500:
        raise ValueError("A ComfyUI API workflow must be a non-empty node object with at most 500 nodes.")
    result = {}
    for raw_id, raw_node in value.items():
        ident = str(raw_id)
        if not ident.isdigit() or not isinstance(raw_node, dict):
            raise ValueError("Every API workflow node needs a numeric ID and an object value.")
        kind, inputs = raw_node.get("class_type"), raw_node.get("inputs")
        if not isinstance(kind, str) or not kind or not isinstance(inputs, dict):
            raise ValueError("Every API workflow node needs class_type and inputs.")
        result[ident] = copy.deepcopy(raw_node)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 2_000_000:
        raise ValueError("The ComfyUI API workflow is too large.")
    conditioning = [node["class_type"] for node in result.values()
                    if node["class_type"] in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo")]
    if len(conditioning) != 1:
        raise ValueError("The imported workflow needs exactly one MiniMax H3 ReferenceToVideo or ImageToVideo node.")
    if sum(node["class_type"] == "SaveVideo" for node in result.values()) != 1:
        raise ValueError("The imported workflow needs exactly one SaveVideo output node.")
    modes = ["ref2va"] if conditioning[0] == "MiniMaxH3ReferenceToVideo" else ["i2va", "fl2va", "l2va", "t2va"]
    return result, modes


class VideoWorkflowManager:
    def __init__(self, data_dir):
        self.directory = (Path(data_dir).resolve() / "video_workflows").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, ident):
        return self.directory / (safe_id(ident) + ".json")

    def _validate(self, value):
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("This is not a supported video workflow profile.")
        ident = safe_id(value.get("id"))
        graph, modes = _graph(value.get("graph"))
        created = value.get("created_at")
        if type(created) not in (int, float) or not math.isfinite(created) or created < 0:
            raise ValueError("Video workflow creation time is invalid.")
        return {
            "schema_version": 1, "id": ident,
            "name": _text(value.get("name"), "Workflow name", 120) or "Imported H3 workflow",
            "description": _text(value.get("description"), "Workflow description", 1000),
            "created_at": float(created), "modes": modes, "builtin": False, "graph": graph,
        }

    def list(self):
        result = [copy.deepcopy(BUILTIN), copy.deepcopy(REF8_BUILTIN)]
        imported = []
        for path in self.directory.glob("*.json"):
            try:
                value = self._validate(json.loads(path.read_text(encoding="utf-8")))
                if value["id"] not in ("builtin", REF8_WORKFLOW_ID):
                    imported.append({key: copy.deepcopy(value[key]) for key in
                                     ("id", "name", "description", "created_at", "modes", "builtin")})
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return [*result, *sorted(imported, key=lambda item: item["created_at"], reverse=True)]

    def get(self, ident):
        if ident in (None, "", "builtin"):
            return copy.deepcopy(BUILTIN)
        if ident == REF8_WORKFLOW_ID:
            graph, modes = _graph(json.loads(REF8_GRAPH_PATH.read_text(encoding="utf-8")))
            return {**copy.deepcopy(REF8_BUILTIN), "schema_version": 1, "graph": graph, "modes": modes}
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("The selected ComfyUI video workflow no longer exists.")
        return self._validate(json.loads(path.read_text(encoding="utf-8")))

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {"name", "description", "graph"}:
            raise ValueError("Import only a workflow name, description and ComfyUI API graph.")
        graph, modes = _graph(body.get("graph"))
        value = self._validate({
            "schema_version": 1, "id": str(uuid.uuid4()), "created_at": time.time(),
            "name": body.get("name"), "description": body.get("description"),
            "modes": modes, "graph": graph,
        })
        atomic_json(self._path(value["id"]), value)
        return {key: copy.deepcopy(value[key]) for key in
                ("id", "name", "description", "created_at", "modes", "builtin")}

    def delete(self, ident):
        if ident in ("builtin", REF8_WORKFLOW_ID):
            raise ValueError("Built-in video workflows cannot be deleted.")
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("The selected video workflow no longer exists.")
        path.unlink()
        return {"deleted": True, "id": safe_id(ident)}
