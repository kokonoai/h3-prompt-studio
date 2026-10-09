"""Pre-render quality gate for production H3 prompts.

The storyboard contract is authoritative.  Local AI may improve staging, but a
render must not begin until the generated plan still preserves the locked
story/dialogue/cast planes and the compiled delivery prompt retains its audio,
language and continuity guards.  These checks are deliberately text-only and
cheap; pixel defects remain the responsibility of post-render review.
"""
from __future__ import annotations

import copy
import hashlib
import re
import time

from .dialogue_audio import nonverbal_vocal_events, speech_like_events
from .prompt_language import PROJECT_DIALOGUE_LANGUAGES
from .productions import (_action_beat_count, _camera_view_count,
                          _explicit_cut_count, _hard_editorial_cut_count)


MAX_AUTOMATIC_REPAIRS = 2
_DIALOGUE = re.compile(r"<d>\[([^\]\r\n]+)\]\s?(.*?)</d>", re.DOTALL)
_FACE_FRONT = re.compile(
    r"(?:front[- ]facing|face\s+(?:toward|to)\s+(?:the\s+)?camera|正面脸|正面臉|正对镜头|"
    r"正對鏡頭|カメラ正面)", re.IGNORECASE)
_SCREEN_FRONT = re.compile(
    r"(?:screen\s+(?:faces?|facing)\s+(?:the\s+)?camera|readable\s+(?:phone\s+)?screen|"
    r"phone\s+screen\s+front|屏幕正对镜头|螢幕正對鏡頭|手机屏幕正面|手機螢幕正面|"
    r"画面をカメラ正面)", re.IGNORECASE)


def _issue(code, message, repair_instruction, *, repairable=True, severity="error"):
    return {
        "severity": "warning" if severity == "warning" else "error",
        "code": code, "message": str(message)[:600],
        "repair_instruction": str(repair_instruction)[:600],
        "repairable": bool(repairable),
    }


def _report(issues, *, attempts=0, repairs=(), prompt=""):
    clean = []
    seen = set()
    for row in issues:
        key = (row.get("code"), row.get("message"))
        if key not in seen:
            seen.add(key)
            clean.append(row)
    return {
        "status": "failed" if any(row.get("severity") == "error" for row in clean) else "passed",
        "attempts": int(attempts),
        "issues": clean[:16],
        "repairs": [str(value)[:600] for value in repairs][:16],
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else "",
        "checked_at": round(time.time(), 3),
    }


def _shot_text(project):
    values = []
    for shot in project.get("shots", []) if isinstance(project, dict) else []:
        if not isinstance(shot, dict):
            continue
        values.extend(str(shot.get(key) or "") for key in
                      ("setting", "action", "performance", "sound", "final_state", "transition"))
        camera = shot.get("camera", {})
        if isinstance(camera, dict):
            values.extend(str(value or "") for value in camera.values())
    if isinstance(project, dict):
        values.append(str(project.get("soundscape") or ""))
    return "\n".join(values)


def _subject_names(project):
    return {
        str(item.get("id") or ""): str(item.get("name") or "").strip()
        for item in project.get("subjects", []) if isinstance(item, dict)
        if str(item.get("id") or "") and str(item.get("name") or "").strip()
    } if isinstance(project, dict) else {}


def _filmable_direction(value):
    """Exclude machine guard blocks from action-density estimates.

    Materialised production actions append cast/device/prop contracts after the
    authored performance paragraph. Their sentences are constraints, not
    sequential actor beats, and counting them made a five-second clip appear
    to contain dozens of actions during the safe fallback path.
    """
    paragraphs = []
    for part in re.split(r"\n{2,}", str(value or "")):
        heading = part.strip().splitlines()[0] if part.strip() else ""
        is_guard = (
            heading == heading.upper() and
            any(token in heading for token in (" LOCK", " OVERRIDE", " CONTRACT"))
        )
        if part.strip() and not is_guard:
            paragraphs.append(part.strip())
    return "\n".join(paragraphs)


def review_generated_plan(production, segment, baseline, candidate):
    """Check an AI staging proposal before the expensive language/render pass."""
    issues = []
    baseline_shots = baseline.get("shots", []) if isinstance(baseline, dict) else []
    candidate_shots = candidate.get("shots", []) if isinstance(candidate, dict) else []
    if len(candidate_shots) != len(baseline_shots):
        issues.append(_issue(
            "scene_count_changed", "The generated prompt changed the locked scene count.",
            "Keep exactly the existing single filmable scene and do not add cuts or sub-scenes."))

    for key, label in (("duration", "duration"), ("production_language", "dialogue language"),
                       ("story", "source story"), ("style", "visual style")):
        if candidate.get(key) != baseline.get(key):
            issues.append(_issue(
                f"locked_{key}_changed", f"The generated prompt changed the locked {label}.",
                f"Restore the exact locked {label}; improve staging only."))

    locked_fields = ("setting", "final_state", "visible_subject_ids", "display_subject_ids",
                     "imagined_subject_ids", "offscreen_subject_ids")
    for index, source in enumerate(baseline_shots):
        if index >= len(candidate_shots) or not isinstance(source, dict) or not isinstance(candidate_shots[index], dict):
            continue
        target = candidate_shots[index]
        if target.get("dialogue", []) != source.get("dialogue", []):
            issues.append(_issue(
                "dialogue_changed", "The generated plan omitted, altered, duplicated or reassigned locked dialogue.",
                "Copy every locked dialogue object exactly once, in its original order and with its original speaker."))
        locks = set(source.get("director_locks", []))
        for field in locked_fields:
            if field in locks and target.get(field) != source.get(field):
                issues.append(_issue(
                    f"locked_{field}_changed", f"The generated plan changed locked shot field {field}.",
                    f"Restore {field} exactly; do not change physical, display, memory or off-screen cast placement."))
        planes = [set(target.get(field, [])) for field in
                  ("visible_subject_ids", "display_subject_ids", "imagined_subject_ids", "offscreen_subject_ids")]
        if any(planes[left] & planes[right] for left in range(len(planes))
               for right in range(left + 1, len(planes))):
            issues.append(_issue(
                "cast_plane_overlap", "A character occupies more than one physical/display/memory/off-screen plane.",
                "Assign each identity to exactly one declared plane; never turn a remote or remembered person into a second body."))

    text = _shot_text(candidate)
    names = _subject_names(candidate)
    source_text = "\n".join([
        _shot_text(baseline),
        *(str(segment.get(key) or "") for key in
          ("story", "setting", "action", "ending", "prompt_direction")),
    ])
    source_events = nonverbal_vocal_events(source_text, names)
    for clause in speech_like_events(text):
        issues.append(_issue(
            "unstructured_speech_event",
            "Non-dialogue direction contains a speech-like vocal event: " + clause,
            "Move any actual words, whisper, narration, singing or voice-over into one locked structured dialogue cue; otherwise restage it as silent visual performance."))
    for event in nonverbal_vocal_events(text, names):
        if not event["performer"]:
            issues.append(_issue(
                "unanchored_nonverbal_vocal",
                f"A wordless {event['cue']} is not assigned to one named performer.",
                "Either name exactly one character and keep the sound brief and wordless, or make the reaction explicitly silent/visual-only."))
            continue
        authorised = any(
            source["cue"] == event["cue"] and source["performer"] == event["performer"]
            for source in source_events)
        if not authorised:
            issues.append(_issue(
                "invented_nonverbal_vocal",
                f"The generated plan invented a {event['cue']} for {event['performer']} outside the authored clip.",
                "Remove the invented sound and retain only visual acting, unless the authored storyboard explicitly assigns that named character one brief wordless event."))
    cut_count = _explicit_cut_count(text)
    hard_cut_count = _hard_editorial_cut_count(text)
    if cut_count > 1:
        issues.append(_issue(
            "multiple_internal_cuts", f"The generated prompt contains {cut_count} internal cuts.",
            "Rewrite all events as one uninterrupted camera setup with no cut, montage, inset or reverse angle."))
    elif hard_cut_count:
        issues.append(_issue(
            "internal_editorial_cut", "The generated prompt contains a hard, match, jump or time/location cut.",
            "Keep one continuous filmable setup; express emphasis through blocking, focus or a physically continuous move."))
    elif cut_count == 1:
        issues.append(_issue(
            "internal_cut", "The generated prompt still contains one editorial cut cue.",
            "Replace the cut with a continuous pan, rack focus, actor movement or reframing without changing story order."))

    camera_views = _camera_view_count(text)
    if camera_views >= 2:
        issues.append(_issue(
            "multiple_camera_views", f"The generated prompt names {camera_views} distinct camera views in one render.",
            "Choose one coherent camera setup and describe only a physically continuous movement within it."))

    duration = int(segment.get("duration") or candidate.get("duration") or 10)
    action_text = "\n".join(_filmable_direction(shot.get(key)) for shot in candidate_shots
                            for key in ("action", "performance") if isinstance(shot, dict))
    beats = _action_beat_count(action_text)
    if beats > max(5, (duration + 1) // 2):
        issues.append(_issue(
            "dense_generated_action", f"The generated prompt packs about {beats} action beats into {duration} seconds.",
            "Keep every required story event but remove decorative micro-actions and overlap compatible reactions in parallel.",
            severity="warning"))

    if (_FACE_FRONT.search(text) and _SCREEN_FRONT.search(text) and
            segment.get("device_view") not in ("screen", "remote_panel")):
        issues.append(_issue(
            "face_screen_geometry", "The generated prompt asks one camera to see both a frontal face and a frontal readable screen.",
            "Choose either the performance angle or the readable screen angle; do not require both frontally in one setup."))
    return _report(issues)


def review_compiled_prompt(production, segment, project, prompt, *, attempts=0, repairs=()):
    """Validate the exact final text that would be submitted to ComfyUI."""
    issues = []
    names = _subject_names(project)
    non_dialogue_text = _shot_text(project)
    for clause in speech_like_events(non_dialogue_text):
        issues.append(_issue(
            "unstructured_speech_event",
            "A sound or action field contains speech-like vocal direction outside structured dialogue: " + clause,
            "Move any actual words, whisper, narration, singing, voice-over or audible voice into a locked structured dialogue cue; otherwise mark the beat silent/visual-only.",
            repairable=False))
    for event in nonverbal_vocal_events(non_dialogue_text, names):
        if not event["performer"]:
            issues.append(_issue(
                "unanchored_nonverbal_vocal",
                f"A wordless {event['cue']} is not assigned to one named performer.",
                "Name exactly one character for the brief wordless event or make it silent visual acting.",
                repairable=False))
    dialogue = [line for shot in project.get("shots", []) for line in shot.get("dialogue", [])
                if isinstance(line, dict) and str(line.get("text") or "").strip()]
    cues = list(_DIALOGUE.finditer(prompt or ""))
    if len(cues) != len(dialogue):
        issues.append(_issue(
            "dialogue_cue_count", f"The final H3 prompt contains {len(cues)} dialogue cues but the locked clip contains {len(dialogue)}.",
            "Rebuild the delivery prompt with every locked dialogue line exactly once.", repairable=False))
    expected_label = PROJECT_DIALOGUE_LANGUAGES.get(
        production.get("language") or project.get("production_language") or "zh-CN", ("", ""))[0]
    if any(match.group(1) != expected_label for match in cues):
        issues.append(_issue(
            "dialogue_language_tag", "A final dialogue cue uses the wrong project-language tag.",
            "Rebuild the language pass using the selected project dialogue language.", repairable=False))
    if (prompt or "").count("FINAL AUDIO OVERRIDE") != 1:
        issues.append(_issue(
            "final_audio_lock", "The final H3 prompt must contain exactly one final audio override.",
            "Recompile the prompt so the exclusive spoken-cue count appears exactly once at the end.", repairable=False))
    expected_count = "Spoken-utterance count is exactly " + (
        "zero" if not dialogue else f"{len(dialogue)} " + ("utterance" if len(dialogue) == 1 else "utterances"))
    if expected_count not in (prompt or ""):
        issues.append(_issue(
            "spoken_count_lock", "The final audio override does not match the locked dialogue count.",
            "Recompile the final audio lock from the current structured dialogue.", repairable=False))
    if project.get("production_render_override") and (prompt or "").count("production_render_override:") != 1:
        issues.append(_issue(
            "continuity_lock_missing", "The final H3 prompt lost or duplicated its production continuity override.",
            "Recompile from the current production scene so the continuity override appears exactly once.", repairable=False))
    for block in project.get("h3_verbatim_blocks", []):
        if block and (prompt or "").count(block) != 1:
            issues.append(_issue(
                "voice_card_changed", "A selected verbatim voice-card block was omitted, rewritten or duplicated.",
                "Restore the selected voice-card block verbatim exactly once.", repairable=False))

    return _report(issues, attempts=attempts, repairs=repairs, prompt=prompt or "")


def repair_request(report):
    """Return a bounded direction for one automatic prompt-only repair."""
    instructions = []
    blocking = [row for row in report.get("issues", []) if row.get("severity") == "error"]
    for row in blocking:
        text = str(row.get("repair_instruction") or row.get("message") or "").strip()
        if text and text not in instructions:
            instructions.append(text)
    return (
        "PRE-RENDER PROMPT QUALITY REPAIR — preserve the exact source story, dialogue, "
        "speaker assignment, cast identities, card bindings, visual style, duration and ending. "
        "Correct only these prompt defects: " + " ".join(instructions[:8])
    )[:4000]


def with_repair_history(report, failures):
    result = copy.deepcopy(report)
    repairs = []
    for failure in failures:
        repairs.extend(str(row.get("message") or "") for row in failure.get("issues", []))
    result["attempts"] = min(MAX_AUTOMATIC_REPAIRS, len(failures))
    result["repairs"] = list(dict.fromkeys(value for value in repairs if value))[:16]
    return result
