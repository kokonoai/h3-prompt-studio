"""Shared H3 dialogue-boundary instructions for every prompt renderer."""
from __future__ import annotations

import copy
import re


_UNANSWERED_WAIT = re.compile(
    r"\b(?:waits?|waiting|awaits?|awaiting)\s+for\s+(?:an?\s+)?(?:answer|response|reply)\b",
    re.IGNORECASE,
)


# Wordless human sounds are not dialogue, but joint audio/video models can turn
# an unbounded acting note into extra speech.  Keep a deliberately small,
# recognisable vocabulary and require every audible event to be tied to one
# named performer.  Metadata such as "her voice is warm" is not an event.
_NONVERBAL_VOCAL_PATTERNS = {
    "sigh": re.compile(r"\b(?:sigh|sighs|sighed|sighing)\b|叹气|叹息|嘆氣|嘆息|ため息", re.IGNORECASE),
    "gasp": re.compile(r"\b(?:gasp|gasps|gasped|gasping)\b|倒吸(?:一口)?气|倒吸(?:一口)?氣|息をのむ", re.IGNORECASE),
    "laugh": re.compile(r"\b(?:laugh|laughs|laughed|laughing|laughter|chuckle|chuckles|chuckled|chuckling|giggle|giggles|giggled|giggling)\b|笑声|笑聲|轻笑|輕笑|发笑|發笑|笑い声|くすくす笑", re.IGNORECASE),
    "sob": re.compile(r"\b(?:sob|sobs|sobbed|sobbing|whimper|whimpers|whimpered|whimpering)\b|呜咽|嗚咽|啜泣|すすり泣", re.IGNORECASE),
    "grunt": re.compile(r"\b(?:grunt|grunts|grunted|grunting|groan|groans|groaned|groaning)\b|闷哼|悶哼|うめき声", re.IGNORECASE),
    "hum": re.compile(r"\b(?:hum|hums|hummed|humming)\b|哼唱|哼着|哼著|鼻歌", re.IGNORECASE),
    "cough": re.compile(r"\b(?:cough|coughs|coughed|coughing|throat[- ]clear(?:ing|s|ed)?)\b|咳嗽|清(?:了)?清嗓|咳払い", re.IGNORECASE),
}
_SPEECH_LIKE_EVENT = re.compile(
    r"\b(?:whisper|whispers|whispered|whispering|mutter|mutters|muttered|muttering|"
    r"narrat(?:e|es|ed|ing|ion)|voice[- ]?over|speaks?|spoke|spoken|says?|said|"
    r"shouts?|shouted|yells?|yelled|chants?|chanted|sing(?:s|ing)?|sang|sung|lyrics?)\b|"
    r"\bvoices?\s+(?:(?:is|are)\s+)?(?:heard|speaks?|calls?|answers?|echoes?|comes?)\b|"
    r"耳语|耳語|低语|低語|喃喃|旁白|画外音|畫外音|说话|說話|开口说|開口說|"
    r"喊道|唱出|歌词|歌詞|ささや|つぶや|ナレーション|話す|歌詞",
    re.IGNORECASE,
)
_NONHUMAN_HUM = re.compile(
    r"\b(?:electrical|electronic|machine|motor|engine|refrigerator|air conditioner|"
    r"fluorescent|transformer|appliance|device|phone|computer|room tone)\b|"
    r"电流|電流|机器|機器|机械|機械|马达|馬達|引擎|冰箱|空调|空調|荧光灯|螢光燈|"
    r"機械音|モーター|冷蔵庫|エアコン",
    re.IGNORECASE,
)
_VOICE_WORD = re.compile(r"\bvoices?\b|人声|人聲|说话声|說話聲|話し声", re.IGNORECASE)
_VOICE_METADATA = re.compile(
    r"\bvoice\s+(?:identity|card|profile|style|timbre|tone|pitch|pace|quality|reference|"
    r"target language)\b|\bvoice\s+(?:is|should be|has)\s+(?:warm|soft|deep|bright|stable|"
    r"restrained|gentle|raspy|husky|natural|consistent)\b|声线|聲線|音色|声質|ボイスカード",
    re.IGNORECASE,
)
_SILENT_OR_NEGATED = re.compile(
    r"\b(?:no|not|never|without)\b.{0,28}\b(?:sound|audible|voice|vocal|"
    r"laugh|gasp|sigh|hum|whisper|speech)\b|\b(?:silent|silently|visual[- ]only|"
    r"inaudible)\b|无声|無聲|不出声|不出聲|纯视觉|純視覺|仅表演|僅表演|"
    r"声に出さず|無音|視覚のみ",
    re.IGNORECASE,
)

_TAGGED_PHONE_PERFORMANCE = re.compile(
    r"\b(?:speaks?|spoke|whispers?|whispered|mutters?|muttered|says?|said)\s+"
    r"(?:into|on|through)\s+(?:(?:the|a|his|her|their)\s+)?"
    r"(?:smartphone|phone|handset)\b",
    re.IGNORECASE,
)
_TAGGED_LINE_PERFORMANCE = re.compile(
    r"\b(?:speaks?|spoke|whispers?|whispered|mutters?|muttered|says?|said)\s+"
    r"(?:(?:his|her|their|the)\s+)?(?:line|dialogue)\b",
    re.IGNORECASE,
)
_TAGGED_REMOTE_PHONE_AUDIO = re.compile(
    r"\b(?:sound\s+of\s+)?(?:an?\s+)?voice\s+"
    r"(?:coming|comes|heard|speaks|calls|answers|echoes)\s+"
    r"(?:from|through|over)\s+(?:(?:the|a)\s+)?"
    r"(?:smartphone|phone)(?:\s+speaker)?\b",
    re.IGNORECASE,
)
_TAGGED_POSSESSIVE_REMOTE_AUDIO = re.compile(
    r"\b(?:[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,3}'s\s+)?voice\s+"
    r"(?:(?:is|was)\s+)?(?:coming|comes|heard|speaks|calls|answers|echoes)\s+"
    r"(?:from|through|over)\s+(?:(?:the|a)\s+)?"
    r"(?:device|smartphone|phone)(?:\s+speaker)?\b",
    re.IGNORECASE,
)
_UNSCRIPTED_PHONE_RESPONSE = re.compile(
    r"\b(?:while\s+)?listening\s+to\s+(?:(?:the|a|an)\s+)?"
    r"(?:response|reply|answer)\b",
    re.IGNORECASE,
)


def _clauses(value):
    return [part.strip() for part in re.split(r"[.!?。！？;；\n]+", _text(value)) if part.strip()]


def _performer(clause, names):
    matches = []
    for label in dict.fromkeys(_text(value) for value in (names or {}).values() if _text(value)):
        if re.search(r"(?<![\w])" + re.escape(label) + r"(?![\w])", clause, re.IGNORECASE):
            matches.append(label)
    return matches[0] if len(matches) == 1 else ""


def nonverbal_vocal_events(value, names=None):
    """Return bounded wordless vocal events found in production prose.

    Each row records a canonical cue and its named performer.  An empty
    performer is intentionally preserved so the prompt-quality gate can ask
    the planner to anchor or remove the ambiguous sound.
    """
    events = []
    for clause in _clauses(value):
        if _SILENT_OR_NEGATED.search(clause):
            continue
        performer = _performer(clause, names or {})
        for cue, pattern in _NONVERBAL_VOCAL_PATTERNS.items():
            if pattern.search(clause):
                if cue == "hum" and not performer and _NONHUMAN_HUM.search(clause):
                    continue
                row = {"cue": cue, "performer": performer, "clause": clause[:360]}
                if not any(item["cue"] == cue and item["performer"] == performer for item in events):
                    events.append(row)
    return events


def speech_like_events(value):
    """Return non-dialogue prose that could be rendered as words or narration."""
    return [clause[:360] for clause in _clauses(value)
            if not _SILENT_OR_NEGATED.search(clause) and
            (_SPEECH_LIKE_EVENT.search(clause) or
             (_VOICE_WORD.search(clause) and not _VOICE_METADATA.search(clause)))]


def normalise_structured_dialogue_directions(project):
    """Bind generic phone-performance prose to existing dialogue cues.

    Reword only narrow, cue-backed phone directions. Unscripted sayings,
    whispers, narration and device voices remain untouched and are still
    rejected by the quality gate.
    """
    result = copy.deepcopy(project)
    for shot in result.get("shots", []) if isinstance(result, dict) else []:
        if not isinstance(shot, dict):
            continue
        speaker_ids = {
            str(line.get("speaker_id") or "")
            for line in shot.get("dialogue", []) if isinstance(line, dict)
            and str(line.get("text") or "").strip()
        }
        if not speaker_ids:
            continue
        visible = set(shot.get("visible_subject_ids", []))
        remote = set(shot.get("display_subject_ids", [])) | set(
            shot.get("imagined_subject_ids", [])) | set(shot.get("offscreen_subject_ids", []))
        has_local_dialogue = bool(speaker_ids & visible)
        has_remote_dialogue = bool(speaker_ids & remote)
        for field in ("action", "performance", "sound"):
            value = shot.get(field)
            if not isinstance(value, str) or not value:
                continue
            if has_local_dialogue:
                value = _TAGGED_PHONE_PERFORMANCE.sub(
                    "performs the already-tagged phone dialogue exactly once through the phone", value)
                value = _TAGGED_LINE_PERFORMANCE.sub(
                    "performs the already-tagged dialogue exactly once", value)
            if has_remote_dialogue:
                value = _TAGGED_REMOTE_PHONE_AUDIO.sub(
                    "the already-tagged remote phone dialogue through the smartphone speaker", value)
                value = _TAGGED_POSSESSIVE_REMOTE_AUDIO.sub(
                    "the already-tagged remote dialogue exactly once through the device speaker", value)
            else:
                # A local phone line does not authorise an improvised answer.
                # Keep the device/environment texture but make the absent
                # response explicit instead of asking the model to repair it.
                value = _TAGGED_REMOTE_PHONE_AUDIO.sub(
                    "a brief non-vocal smartphone-speaker noise; no reply occurs in this clip", value)
                value = _TAGGED_POSSESSIVE_REMOTE_AUDIO.sub(
                    "a brief non-vocal device-speaker noise; no reply occurs in this clip", value)
                value = _UNSCRIPTED_PHONE_RESPONSE.sub(
                    "holding the listening posture in silence; no reply occurs in this clip", value)
            shot[field] = value
    return result


def silence_unstructured_speech_directions(project, authored_project=None):
    """Deterministically neutralise remaining prose that could invent vocals.

    This is used both to derive the locked audio-safe planning baseline and as
    the last-resort repair for an AI-generated candidate. Structured dialogue
    remains byte-for-byte in the shot; only duplicate/invented speech and
    unauthorised wordless vocal wording is replaced. When the authored project
    is supplied, an explicitly named authored sigh/laugh/etc. is preserved.
    Storyboard coverage and speaker assignment are therefore unchanged.
    """
    result = copy.deepcopy(project)
    names = {
        str(item.get("id") or ""): str(item.get("name") or "").strip()
        for item in result.get("subjects", []) if isinstance(item, dict)
        if str(item.get("id") or "") and str(item.get("name") or "").strip()
    } if isinstance(result, dict) else {}
    authored_events = set()
    if isinstance(authored_project, dict):
        authored_names = {
            str(item.get("id") or ""): str(item.get("name") or "").strip()
            for item in authored_project.get("subjects", []) if isinstance(item, dict)
            if str(item.get("id") or "") and str(item.get("name") or "").strip()
        }
        authored_events = {
            (event["cue"], event["performer"])
            for event in authorised_nonverbal_vocal_events(
                authored_project.get("shots", []), authored_names,
                authored_project.get("soundscape", ""))
        }
    silent_reactions = {
        "sigh": "silent visible exhale",
        "gasp": "silent startled reaction",
        "laugh": "silent visual amusement",
        "sob": "silent tearful reaction",
        "grunt": "silent exertion reaction",
        "hum": "silent rhythmic motion",
        "cough": "silent throat-clearing gesture",
    }

    def clean_direction(value, replacement):
        if not isinstance(value, str) or not value:
            return value
        for clause in speech_like_events(value):
            value = value.replace(clause, replacement)
        # Preserve the rest of a continuity/final-state sentence. Replacing
        # only the unsafe vocal token keeps positions, props, screen geometry
        # and emotion intact (for example "both remain on their screens,
        # sharing a soft laugh" becomes silent visual amusement rather than
        # losing the complete final-state clause).
        for event in nonverbal_vocal_events(value, names):
            if (event["cue"], event["performer"]) not in authored_events:
                pattern = _NONVERBAL_VOCAL_PATTERNS[event["cue"]]
                value = pattern.sub(silent_reactions[event["cue"]], value)
        return value

    for shot in result.get("shots", []) if isinstance(result, dict) else []:
        if not isinstance(shot, dict):
            continue
        dialogue = [line for line in shot.get("dialogue", [])
                    if isinstance(line, dict) and str(line.get("text") or "").strip()]
        replacement = (
            "During this beat, the assigned speaker or speakers perform only the already-tagged "
            "dialogue cue exactly once; no other speech occurs"
            if dialogue else
            "All characters remain silent during this beat; show the reaction visually only and no speech occurs"
        )
        # ``setting`` and ``final_state`` are locked fields, but they are still
        # non-dialogue prompt prose and can accidentally author extra voices.
        # Callers normalise the locked baseline and generated candidate with
        # this same deterministic transform, so source dialogue and story
        # coverage remain untouched while the renderer receives a safe audio
        # contract.
        for field in ("setting", "action", "performance", "sound", "final_state"):
            value = shot.get(field)
            shot[field] = clean_direction(value, replacement)
        camera = shot.get("camera")
        if isinstance(camera, dict):
            for key, value in camera.items():
                camera[key] = clean_direction(value, replacement)
    if isinstance(result, dict):
        result["soundscape"] = clean_direction(
            result.get("soundscape"),
            "Only non-vocal ambience is audible; no speech occurs",
        )
    return result


def authorised_nonverbal_vocal_events(shots, names, soundscape=""):
    """Collect exact wordless events that the final audio lock may permit."""
    events = []
    values = [soundscape]
    for shot in shots if isinstance(shots, list) else []:
        if isinstance(shot, dict):
            values.extend(shot.get(key, "") for key in ("action", "performance", "sound"))
    for value in values:
        for event in nonverbal_vocal_events(value, names):
            # Unassigned cues stay visual/silent; they never become an audio
            # permission merely because an AI planner used a vague pronoun.
            if event["performer"] and not any(
                    row["cue"] == event["cue"] and row["performer"] == event["performer"]
                    for row in events):
                events.append(event)
    return events


def _nonverbal_lock(events):
    if not events:
        return (
            "AUTHORIZED NONVERBAL VOCAL EVENTS: none. Acting notes for smiles, tears, "
            "breathing or reactions remain silent visual performance unless explicitly listed here."
        )
    labels = "; ".join(
        f"{event['performer']}=one brief wordless {event['cue']}" for event in events)
    return (
        "AUTHORIZED NONVERBAL VOCAL EVENTS: " + labels + ". Each listed event occurs at most once, "
        "contains no words or lyrics, and does not change the spoken-utterance count. No other human "
        "vocal sound is permitted."
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


def audible_speech_lock(shots, names, soundscape=""):
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
    nonverbal = authorised_nonverbal_vocal_events(shots, names, soundscape)
    nonverbal_lock = _nonverbal_lock(nonverbal)

    if not dialogue:
        return (
            "AUDIBLE SPEECH LOCK — CURRENT CLIP: No audible dialogue, narration, "
            "singing, chanting, whispering, muttering, crowd speech or invented "
            "speech-like vocalisation occurs in this clip. Text outside structured "
            "dialogue is silent production direction, not words to vocalise. "
            + nonverbal_lock + " Use only the explicitly requested ambience and effects."
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
        + "follow-up words, ad-libs, whispers, muttering, crowd speech or speech-like "
        + "vocalisation. " + nonverbal_lock + " Waiting for an answer or response is visual "
        + "acting only unless a later structured dialogue line explicitly supplies that "
        + "reply. Do not fill unused clip duration with new voices. After the final scripted "
        + "line, only the explicitly requested non-speech ambience is audible."
    )


def final_audio_override(shots, names, soundscape=""):
    """Place a compact, highest-priority speech boundary at prompt end.

    Long production prompts contain voice cards, performance prose, source
    coverage and sound-effect labels after the main dialogue block.  Joint
    audio/video models can occasionally treat one of those later strings as a
    fresh utterance even though the earlier speech lock is correct.  This tail
    deliberately does not repeat the dialogue text (which could itself cause a
    duplicate delivery); it counts and orders the existing ``<d>`` cues and
    makes every other vocal event illegal.
    """
    dialogue = []
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        for line in shot.get("dialogue", []):
            if isinstance(line, dict) and _text(line.get("text")):
                dialogue.append(line)
    nonverbal = authorised_nonverbal_vocal_events(shots, names, soundscape)
    nonverbal_lock = _nonverbal_lock(nonverbal)

    if not dialogue:
        return (
            "FINAL AUDIO OVERRIDE — HIGHEST PRIORITY: Spoken-utterance count is exactly zero. "
            "No character, narrator, background figure, device, memory, reflection or sound effect "
            "may produce words or speech-like vocal sounds. " + nonverbal_lock + " Render only "
            "the explicitly requested ambience and effects."
        )

    cue_speakers = []
    for index, line in enumerate(dialogue, 1):
        sid = line.get("speaker_id")
        label = _text(names.get(sid)) if isinstance(names, dict) else ""
        cue_speakers.append(f"cue {index}={label or 'assigned speaker'}")
    count = len(dialogue)
    noun = "utterance" if count == 1 else "utterances"
    return (
        "FINAL AUDIO OVERRIDE — HIGHEST PRIORITY: Spoken-utterance count is exactly "
        + str(count) + " " + noun + " total (" + "; ".join(cue_speakers) + "). "
        "Perform each existing tagged dialogue cue exactly once, in listed order, without overlap or repetition. "
        "The tagged dialogue cues are the only words in the clip. Character names, acting notes, voice-card text, "
        "source-coverage text, headings, camera directions and sound-effect labels are silent metadata "
        "and must never be read aloud. Quoted onomatopoeia describes a non-vocal effect, not a spoken "
        "word. Do not add a narrator, reaction voice, reply, ad-lib, whisper, chant, crowd voice or "
        "background conversation. " + nonverbal_lock + " During every gap and after the final cue, "
        "only the requested ambience or effects continue."
    )
