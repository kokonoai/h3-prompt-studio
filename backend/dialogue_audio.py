"""Shared H3 dialogue-boundary instructions for every prompt renderer."""
from __future__ import annotations

import re


_UNANSWERED_WAIT = re.compile(
    r"\b(?:waits?|waiting|awaits?|awaiting)\s+for\s+(?:an?\s+)?(?:answer|response|reply)\b",
    re.IGNORECASE,
)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def clarify_unanswered_wait(value, shots):
    """Keep an expectant acting beat without inviting an unscripted reply."""
    count = sum(
        1
        for shot in shots if isinstance(shot, dict)
        for line in shot.get("dialogue", []) if isinstance(line, dict) and _text(line.get("text"))
    )
    text = value if isinstance(value, str) else ""
    if count > 1 or not text:
        return text
    return _UNANSWERED_WAIT.sub(
        "holds the expectant beat in silence; no reply occurs within this clip",
        text,
    )


def audible_speech_lock(shots, names):
    """Describe the complete audible roster without repeating spoken words.

    H3 generates audio jointly with video rather than using a deterministic TTS
    pass.  A short scripted line inside a longer clip therefore needs an
    explicit boundary: scene prose, voice cards and a character waiting for an
    answer are not permission to invent more speech.
    """
    dialogue = []
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        for line in shot.get("dialogue", []):
            if isinstance(line, dict) and _text(line.get("text")):
                dialogue.append(line)

    if not dialogue:
        return (
            "AUDIBLE SPEECH LOCK — CURRENT CLIP: No audible dialogue, narration, "
            "singing, chanting, whispering, muttering, crowd speech or speech-like "
            "vocalisation occurs in this clip. All characters keep their mouths "
            "closed. Text outside structured dialogue is silent production direction, "
            "not words to vocalise. Use only the explicitly requested non-speech ambience."
        )

    speakers = []
    for line in dialogue:
        sid = line.get("speaker_id")
        label = _text(names.get(sid)) if isinstance(names, dict) else ""
        if label and label not in speakers:
            speakers.append(label)
    count = len(dialogue)
    line_count = "exactly one structured dialogue line" if count == 1 else f"exactly {count} structured dialogue lines"
    roster = ", ".join(speakers) or "the explicitly assigned speaker or speakers"
    return (
        "AUDIBLE SPEECH LOCK — CURRENT CLIP: The clip contains " + line_count
        + ", and those tagged words are the complete and exclusive audible speech. "
        + "Only " + roster + " may speak, and only for the assigned line or lines. "
        + "Text outside structured dialogue is silent production direction, not speech, "
        + "narration or lyrics, and must never be vocalised. Every character without an "
        + "assigned line remains silent with a closed mouth; do not invent replies, "
        + "follow-up words, ad-libs, whispers, muttering, laughter, gasps, grunts, crowd "
        + "speech or speech-like vocalisation. Waiting for an answer or response is visual "
        + "acting only unless a later structured dialogue line explicitly supplies that "
        + "reply. Do not fill unused clip duration with new voices. After the final scripted "
        + "line, only the explicitly requested non-speech ambience is audible."
    )
