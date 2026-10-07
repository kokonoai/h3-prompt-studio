"""Evidence-bounded quality checks for completed production takes.

The renderer is authoritative for pixels and audio streams.  This module keeps
the cheap media checks deterministic and treats the VLM as a sampled-frame
reviewer, never as proof of dialogue, motion between samples, or off-screen
events.
"""
from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


ISSUE_CODES = (
    "duplicate_character", "unexpected_visible_person", "missing_required_character",
    "identity_drift", "style_drift", "prop_or_device_error",
    "remote_character_spatial_error", "severe_visual_artifact",
)

VISUAL_REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "observed_cast_counts", "issues"],
    "properties": {
        "summary": {"type": "string", "maxLength": 600},
        "observed_cast_counts": {
            "type": "object", "additionalProperties": False,
            "required": ["first", "middle", "last"],
            "properties": {key: {"type": "integer", "minimum": 0, "maximum": 24}
                           for key in ("first", "middle", "last")},
        },
        "issues": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["code", "severity", "confidence", "frame", "message",
                         "repair_instruction"],
            "properties": {
                "code": {"type": "string", "enum": list(ISSUE_CODES)},
                "severity": {"type": "string", "enum": ["warning", "error"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "frame": {"type": "string", "enum": ["first", "middle", "last", "multiple"]},
                "message": {"type": "string", "maxLength": 360},
                "repair_instruction": {"type": "string", "maxLength": 480},
            },
        }},
    },
}

VISUAL_REVIEW_SYSTEM = """You are a conservative post-render continuity inspector.
The supplied contact sheet contains three GENERATED sample frames on the top row
(FIRST, MIDDLE, LAST) and, when available, canonical REFERENCE images below.
Judge only visible evidence. A sample cannot prove dialogue, sound, continuous
motion, or what happened between frames. Never treat a reference image as a
person who should appear in the generated scene.

Report only material defects: duplicated bodies of the same character,
unexpected visible people, a character required at that exact boundary being
absent, strong identity/style drift, wrong phone/prop geometry or ownership,
remote/remembered/off-screen characters incorrectly made physical, or severe
visual artifacts. Use the supplied physical/display/imagined/off-screen rosters
as distinct visual planes: a display-only person may exist inside one bounded
screen or editorial remote panel but must not become a full-size body in the
local room, and screen content must not recursively contain another copy of the
same scene. A normal mirror reflection is not a duplicate body; flag it only
when an extra independent physical instance exists. Do not penalize normal pose,
crop, lighting, expression or camera changes. Missing identity references reduce
confidence; text alone is not proof of an exact face. Use error only when the
sampled pixels show a clear render-breaking contradiction. Provide one concrete
repair instruction per issue and do not invent new story content.

The context distinguishes physical_present_* from required_visible_*. A person
may be physically present in the location while legitimately outside a close-up
or insert frame. Never report a missing character from physical_present_* alone;
only required_visible_first/last are exact boundary requirements. Likewise,
only plot_critical_prop_holders are hard prop requirements. A continuity-only
prop may be cropped or unused in this shot; report a clear contradiction only as
a warning. Missing props and accessories are prop_or_device_error, not
identity_drift."""


def _fit(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail(size)
    canvas = Image.new("RGB", size, (22, 28, 25))
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def contact_sheet(frame_paths: dict[str, Path], references: list[dict]) -> str:
    """Return one bounded JPEG data URL with generated samples and references."""
    frame_size, gap, label_height = (416, 234), 12, 28
    width = frame_size[0] * 3 + gap * 4
    reference_rows = references[:6]
    ref_height = 190 if reference_rows else 0
    height = gap + label_height + frame_size[1] + (gap + label_height + ref_height if reference_rows else gap)
    sheet = Image.new("RGB", (width, height), (11, 18, 15))
    draw = ImageDraw.Draw(sheet)
    for index, kind in enumerate(("first", "middle", "last")):
        x = gap + index * (frame_size[0] + gap)
        draw.text((x + 4, gap + 5), f"GENERATED {kind.upper()}", fill=(205, 244, 227))
        with Image.open(frame_paths[kind]) as source:
            sheet.paste(_fit(source, frame_size), (x, gap + label_height))
    if reference_rows:
        top = gap + label_height + frame_size[1] + gap
        draw.text((gap + 4, top + 5), "CANONICAL REFERENCES (comparison only)", fill=(169, 210, 192))
        cell_width = (width - gap * (len(reference_rows) + 1)) // len(reference_rows)
        for index, row in enumerate(reference_rows):
            x = gap + index * (cell_width + gap)
            with Image.open(row["path"]) as source:
                sheet.paste(_fit(source, (cell_width, ref_height)), (x, top + label_height))
            # ASCII-safe labels remain readable even on systems without a CJK font.
            draw.rectangle((x, top + label_height + ref_height - 22,
                            x + cell_width, top + label_height + ref_height), fill=(8, 14, 12))
            draw.text((x + 4, top + label_height + ref_height - 18),
                      str(row.get("label") or f"REF {index + 1}")[:34], fill=(230, 244, 237))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def media_issues(probe: dict, expected_duration: float, has_dialogue: bool) -> list[dict]:
    streams = probe.get("streams", []) if isinstance(probe, dict) else []
    video = next((row for row in streams if row.get("codec_type") == "video" and
                  not row.get("disposition", {}).get("attached_pic")), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    issues = []
    if not video:
        issues.append({"severity": "error", "code": "missing_video_stream",
                       "confidence": 1.0, "frame": "multiple",
                       "message": "The completed file has no playable video stream.",
                       "repair_instruction": "Render this clip again with a SaveVideo output."})
    duration = None
    try:
        duration = float(probe.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    if duration is None or not math.isfinite(duration) or duration <= 0:
        issues.append({"severity": "error", "code": "invalid_duration",
                       "confidence": 1.0, "frame": "multiple",
                       "message": "The completed file has no valid media duration.",
                       "repair_instruction": "Render and save a complete playable clip."})
    elif expected_duration and abs(duration - expected_duration) > max(1.5, expected_duration * .22):
        issues.append({"severity": "warning", "code": "duration_mismatch",
                       "confidence": 1.0, "frame": "multiple",
                       "message": f"Rendered duration is {duration:.1f}s; the storyboard requests {expected_duration:.1f}s.",
                       "repair_instruction": "Keep the render duration aligned with the storyboard clip."})
    if has_dialogue and not audio:
        issues.append({"severity": "error", "code": "missing_dialogue_audio",
                       "confidence": 1.0, "frame": "multiple",
                       "message": "This clip has authored dialogue but the completed file has no audio stream.",
                       "repair_instruction": "Render with audio enabled and preserve the authored dialogue."})
    return issues


def normalise_visual_review(value: dict, *, has_identity_references: bool,
                            expected_first, expected_last,
                            continuity_prop_names=(), strict_prop_or_device=True) -> dict:
    """Apply conservative local policy to a schema-validated VLM response."""
    def boundary_contract(value):
        if isinstance(value, (list, tuple, set)):
            names = [str(name).strip() for name in value if str(name).strip()]
            return len(names), names
        try:
            return max(0, int(value)), []
        except (TypeError, ValueError):
            return 0, []

    expected_first_count, expected_first_names = boundary_contract(expected_first)
    expected_last_count, expected_last_names = boundary_contract(expected_last)
    continuity_props = [str(name).strip() for name in continuity_prop_names
                        if str(name).strip()]
    counts = value.get("observed_cast_counts", {})
    issues = []
    for raw in value.get("issues", [])[:10]:
        issue = dict(raw)
        confidence = round(float(issue.get("confidence", 0)), 3)
        issue["confidence"] = confidence
        frame = issue.get("frame")
        code = issue.get("code")
        # Three samples cannot establish a mid-shot absence. Boundary absence
        # is actionable only where the contract explicitly requires a body.
        if code == "missing_required_character":
            boundary_count = (expected_first_count if frame == "first" else
                              expected_last_count if frame == "last" else 0)
            boundary_names = (expected_first_names if frame == "first" else
                              expected_last_names if frame == "last" else [])
            evidence_text = " ".join(str(issue.get(key) or "")
                                     for key in ("message", "repair_instruction")).casefold()
            names_match = (not boundary_names or
                           any(name.casefold() in evidence_text for name in boundary_names))
            if not boundary_count or not names_match or frame in ("middle", "multiple"):
                issue["severity"] = "warning"
                issue["confidence"] = min(confidence, .69)
        evidence_text = " ".join(str(issue.get(key) or "")
                                 for key in ("message", "repair_instruction")).casefold()
        continuity_prop = next((name for name in continuity_props
                                if name.casefold() in evidence_text), "")
        if code in ("identity_drift", "prop_or_device_error") and continuity_prop:
            issue["code"] = "prop_or_device_error"
            issue["severity"] = "warning"
            issue["confidence"] = min(confidence, .79)
        elif code == "prop_or_device_error" and not strict_prop_or_device:
            issue["severity"] = "warning"
            issue["confidence"] = min(confidence, .79)
        if code == "identity_drift" and not has_identity_references:
            issue["severity"] = "warning"
            issue["confidence"] = min(confidence, .59)
        issues.append(issue)
    high_errors = [row for row in issues if row.get("severity") == "error" and
                   float(row.get("confidence", 0)) >= .88]
    status = "failed" if high_errors else "warning" if issues else "passed"
    return {"status": status, "summary": str(value.get("summary") or "")[:600],
            "observed_cast_counts": {key: int(counts.get(key, 0))
                                     for key in ("first", "middle", "last")},
            "issues": issues}


def repair_direction(issues: list[dict]) -> str:
    actionable = []
    for row in issues:
        if row.get("severity") != "error" or float(row.get("confidence", 0)) < .88:
            continue
        text = str(row.get("repair_instruction") or row.get("message") or "").strip()
        if text and text not in actionable:
            actionable.append(text)
    if not actionable:
        return ""
    return ("POST-RENDER REPAIR — preserve the exact story, dialogue, cast identities, style and duration. "
            "Correct only these verified defects: " + " ".join(actionable[:6]))[:2000]
