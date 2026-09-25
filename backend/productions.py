"""Long-form production planning built on ordinary H3 Studio projects."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import time
import uuid
from pathlib import Path

from .projects import PROMPT_VERSIONS, atomic_json, check_project, safe_id, shot, uid
from .video_workflows import REF8_WORKFLOW_ID, ref8_recipe_settings

MIN_SECONDS, PLANNED_MIN_SECONDS, DEFAULT_CLIP_SECONDS = 4, 5, 10
MAX_SECONDS, MAX_SEGMENTS, MAX_EPISODES = 15, 64, 100
MAX_SEGMENT_KEYFRAMES = 12
REFERENCE_STRATEGY_VERSION = 3
CAST_TIMELINE_VERSION = 1
TIMING_KEYS = ("episode_plan_seconds", "storyboard_plan_seconds", "merge_seconds")
CARD_KINDS = ("characters", "wardrobe", "props", "environments", "voices", "styles")
SELECTABLE_CARD_KINDS = ("characters", "wardrobe", "props", "environments", "voices")
OVERVIEW_CARD_KINDS = ("characters", "wardrobe", "props", "environments")
CAST_TIMELINE_KEYS = ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")
TRANSITION_MODES = ("continuous", "matched_cut", "hard_cut", "time_jump", "state_change", "insert")
PRODUCTION_LANGUAGES = {
    "zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese",
    "en": "English", "ja": "Japanese",
}
CARD_SEMANTIC_ROLES = {
    "characters": "character", "wardrobe": "wardrobe", "props": "object",
    "environments": "background", "voices": "other", "styles": "style",
}

PRODUCTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["segments"],
    "properties": {"segments": {
        "type": "array", "minItems": 1, "maxItems": MAX_SEGMENTS,
        "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "story", "setting", "action", "ending", "duration",
                         "duration_reason", "dialogue", "image_prompt", "card_selection",
                         "cast_timeline", "transition_mode"],
            "properties": {
                "title": {"type": "string", "maxLength": 120},
                "story": {"type": "string", "maxLength": 3000},
                "setting": {"type": "string", "maxLength": 1000},
                "action": {"type": "string", "maxLength": 3000},
                "ending": {"type": "string", "maxLength": 1200},
                "duration": {"type": "integer", "minimum": PLANNED_MIN_SECONDS, "maximum": MAX_SECONDS},
                "duration_reason": {"type": "string", "maxLength": 500},
                "dialogue": {"type": "array", "maxItems": 16, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["speaker", "text", "language", "voiceover"],
                    "properties": {
                        "speaker": {"type": "string", "maxLength": 100},
                        "text": {"type": "string", "maxLength": 1000},
                        "language": {"type": "string", "maxLength": 60},
                        "voiceover": {"type": "boolean"},
                    }}},
                "image_prompt": {"type": "string", "maxLength": 3000},
                "cast_timeline": {
                    "type": "object", "additionalProperties": False,
                    "required": list(CAST_TIMELINE_KEYS),
                    "properties": {
                        key: {"type": "array", "maxItems": 16,
                              "items": {"type": "string", "maxLength": 120}}
                        for key in CAST_TIMELINE_KEYS
                    }},
                "transition_mode": {"type": "string", "enum": list(TRANSITION_MODES)},
                "card_selection": {
                    "type": "object", "additionalProperties": False,
                    "required": list(SELECTABLE_CARD_KINDS),
                    "properties": {
                        "characters": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 120}},
                        "wardrobe": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 120}},
                        "props": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 120}},
                        "environments": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 120}},
                        "voices": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 120}},
                    }},
            }}}}}

EPISODE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["episodes"],
    "properties": {"episodes": {
        "type": "array", "minItems": 1, "maxItems": MAX_EPISODES,
        "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "logline", "story", "character_names", "continuity_notes"],
            "properties": {
                "title": {"type": "string", "maxLength": 160},
                "logline": {"type": "string", "maxLength": 1200},
                "story": {"type": "string", "maxLength": 20000},
                "character_names": {"type": "array", "maxItems": 64,
                                    "items": {"type": "string", "maxLength": 120}},
                "continuity_notes": {"type": "string", "maxLength": 3000},
            }}}}}

CARD_PLANNER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["series_voice_style", "characters", "wardrobe", "props", "environments", "voices", "styles"],
    "properties": {
        "series_voice_style": {"type": "string", "maxLength": 6000},
        "characters": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "notes"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
            }}},
        "wardrobe": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "notes", "owner_character"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
                "owner_character": {"type": "string", "maxLength": 120},
            }}},
        "props": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "notes", "owner_character"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
                "owner_character": {"type": "string", "maxLength": 120},
            }}},
        "environments": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "notes"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
            }}},
        "voices": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "character_name", "description", "notes", "voice_id", "pace"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "character_name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
                "voice_id": {"type": "string", "maxLength": 100},
                "pace": {"type": "string", "maxLength": 160},
            }}},
        "styles": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "notes"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 3000},
                "notes": {"type": "string", "maxLength": 1200},
            }}},
    },
}

SHOWRUNNER_CORE = """Act as the project's showrunner and continuity supervisor.
- First protect the user's current request, confirmed canon, approved material, continuity, character psychology, causality, world rules, intended emotion, approved style, and production feasibility, in that order.
- Treat supplied reference material as evidence, not permission to overwrite established canon. Do not turn an unapproved suggestion into a confirmed fact.
- Build character-specific drama from want, need, defence, contradiction, choice, action and consequence. Characters must affect events rather than merely receive instructions.
- Track relationship residue, knowledge, promises, setups and payoffs. Consequences survive scene and episode boundaries.
- Every story unit needs one primary dramatic purpose. Prefer behaviour, blocking, reaction, silence, sound and visible consequence over explanatory dialogue.
- Avoid generic AI habits: automatic twists or cliffhangers, equal dialogue for everyone, slogan-like lines, theme speeches, convenient new powers or characters, false complexity, instant emotional repair and stakes inflation.
- Preserve restraint. A smaller choice unique to these characters is better than a generic louder event.
- Silently verify motivation, causality, consequence, emotional change, continuity, style, performability and timing before returning the requested structured result."""

EPISODE_PLANNER_SYSTEM = SHOWRUNNER_CORE + """

You are now the series editor planning episodes before shot generation. Return strict JSON only.
- Produce exactly requested_episode_count episodes in story order. Do not invent major events, characters or dialogue.
- Authored screenplay dialogue is locked source material. When it is already in project_output_language, copy every line verbatim, including interjections, contractions and pauses; never consolidate, polish or omit it. When translation is required, translate once without summarising or combining lines.
- Use project_output_language for every returned text field, regardless of input language.
- Each episode needs a distinct dramatic engine: primary purpose, opening state, active desire or pressure, causal progression, meaningful turn, changed exit state, and a locally satisfying payoff. Use a hook only when the story earns one.
- Respect target_minutes_per_episode as an editorial target, not a reason to pad the story.
- visual_style and long_form_narrative_style shape presentation but never override story facts.
- If visual_style reports style-card image analysis, treat it as highest visual authority; custom text is secondary and the named preset is fallback.
- character_names must contain every character who appears, speaks, is heard, or materially affects that episode.
- Copy character names exactly from available_character_cards when a matching card exists. Never invent a card name.
- continuity_notes must call out returning-character state, relationship changes, wardrobe, props, location, injuries, knowledge, setups awaiting payoff and unresolved actions that the next episode must preserve.
- Keep full usable episode story detail in story; logline is only the short overview."""

PLANNER_SYSTEM = SHOWRUNNER_CORE + """

You are now the production editor for a local H3 video workflow.
Split the story into coherent GENERATION CLIPS, not equal chunks. Return strict JSON only.

TIMING
- Every newly planned clip is an integer 5-15 seconds. Ten seconds is the neutral planning baseline, not a fixed duration.
- The request supplies an episode target, a recommended clip count and a feasible count range. Cover the complete episode, keep the sum of clip durations equal to the supplied target, and normally use the recommended count. Use another count inside the feasible range only when real dramatic beats, dialogue timing or scene boundaries require it.
- Explicit source timecodes are editorial authority. Treat episode_timing.timed_clip_groups as ordered coverage blocks when supplied: never merge across, reorder, omit or overlap those blocks. A dense block may be subdivided into adjacent 5-15 second generation clips only when dialogue timing, several sequential actions, or ensemble complexity genuinely requires it; the subdivided durations must still cover that block exactly.
- Estimate exact dialogue at natural pace, then add visible action, reaction, camera settling and a readable final hold.
- Merge beats under 4 seconds. Split beats over 15 seconds at a natural action, location, time or dramatic boundary.
- Never shorten, paraphrase, reassign or rush exact dialogue.
- Copy every entry in locked_source_dialogue into its matching clip exactly, preserving order, speaker, contractions, interjections and punctuation. Do not merge two authored lines.
- One clip contains one coherent dramatic beat with an entry state, active pressure, visible progression or reaction, and a concrete changed ending state.
- Continuations may reuse about 1.6 seconds of prior motion, so prefer no more than 13 seconds of genuinely new action for a continuous beat.

CONTENT
- Preserve story order, facts, characters, relationships, knowledge, props, wardrobe, location, performance language and visual style.
- For visual treatment, obey this priority: analysed style-card image > custom visual style > style bible > named preset. Never copy depicted content from a style image.
- Write every returned text field, including dialogue, in project_output_language. The input may be in any language.
- Translate dialogue faithfully into project_output_language while preserving meaning, speaker and intent; after translation it becomes exact locked dialogue.
- Do not invent plot events or dialogue. Heard but unseen speech uses voiceover=true.
- setting/action/ending must be concrete and filmable.
- image_prompt describes a useful keyframe: identity, wardrobe, props, composition, light and continuity locks; no text overlays.
- duration_reason names timing drivers so the user can audit the choice.
- Do not force dialogue into a visual beat. Protect reaction time, silence and emotional aftermath when they carry the scene.
- Section headings, part-ending labels, broad time-range labels and planning notes are editorial metadata. Do not turn them into visible action, dialogue, props or spoken narration.

ENSEMBLE GENERATION SAFETY
- Prefer 1-4 visible named identities in one generation clip. When a source block requires more, subdivide at a causal action boundary so each adjacent clip preserves story order and total duration. Do not omit required characters merely to satisfy this preference.
- Give every visible character one stable physical instance and a distinct screen region. Characters with similar palette, size or silhouette need different body geometry and signature markers stated in setting/action/image_prompt. Never solve crowding by duplicating, mirroring, blending, replacing or swapping identities.
- card_selection.characters is the exact on-screen roster for that clip, not everyone mentioned in surrounding continuity or an editorial summary. Off-screen, already-exited, next-clip and merely discussed characters do not belong in that array.
- Fill cast_timeline for every clip. visible_start and visible_end are the physical cast visible at those exact boundaries. enters and exits are narrative entrances/departures during the clip, not ordinary reframing or temporary occlusion. offscreen contains heard or spatially present characters who never become visible in this clip. mentioned_only contains characters discussed only in summaries, memories, prior/future events or continuity notes.
- A character removed, transported, reset, teleported or sent to another location belongs in exits for that clip and stays physically absent from later clips until a later clip explicitly lists that character in enters. Never put an exited character back into visible_start/visible_end/card_selection merely because prose mentions the character or reports its distant sound.
- Walking through a doorway into the next adjoining shot, climbing onward, leaving the current framing, a camera cut, a dissolve or temporary occlusion is NOT a persistent narrative exit. Do not place such continuing performers in exits merely because they leave one composition. Keep them available to the next sequential clip.
- A montage or time-compressed beat that explicitly shows named performers acting must list those performers in card_selection.characters and in visible_start/visible_end/enters/exits as appropriate. Never demote visibly acting performers to mentioned_only.
- card_selection.characters must equal the union of cast_timeline.visible_start, visible_end, enters and exits. It contains an exiting character because that character needs a reference while still visible, but never contains offscreen or mentioned_only characters.
- transition_mode describes the editorial relationship to the previous clip: continuous only for unbroken time/place/action suitable for saved-motion continuation; matched_cut for a deliberate same-scene reframing; hard_cut for an ordinary new shot or location; time_jump for montage or elapsed time; state_change for reset, teleport, transformation or discontinuous world-state change; insert for a detail/cutaway. The first clip is hard_cut.

REFERENCE CARD SELECTION
- For every clip, fill card_selection using exact card names copied only from available_asset_cards.
- characters: visible characters whose identity image is useful. Include a speaking character only when visible.
- wardrobe: only clothing actually worn by a selected visible character in this clip.
- props: only visible, handled, plot-critical or continuity-critical objects. Do not include incidental objects.
- environments: the location/layout actually visible in the clip.
- voices: only characters who speak or provide audible voiceover in this clip.
- Empty arrays are correct when no matching card is needed. Never invent a card name.
- Select every relevant named card even if its individual image is missing. The application can use a user-supplied category overview as fallback.
- Do not optimize around the image limit. The application will prefer user-supplied category overviews when details are missing or the nine-image budget is under pressure, otherwise it may create contact sheets."""

CARD_PLANNER_SYSTEM = SHOWRUNNER_CORE + """

You are now the production bible editor. Analyse the supplied story or screenplay and return strict JSON only.
- Build a compact reusable TEXT card library for the whole production: named characters, recurring or story-significant wardrobe, plot/continuity props, recurring environments, speaking-character voices, and at most two useful visual style cards.
- Use project_output_language for all prose. Keep proper character names exactly as written in the source or existing card catalog.
- Do not invent new plot events, relationships, powers, objects or locations. When appearance or voice is unstated, make only a restrained, production-useful proposal consistent with role, age cues, genre and tone; never present an unsupported story fact.
- Character descriptions cover stable visible identity: apparent age, face, hair, build, silhouette and unmistakable markers. Notes cover identity/relationship/continuity facts that must not drift.
- Character cards are reusable across episodes. Never put this clip's action, current emotion, temporary prop possession, earlier plot events, or another character's present location in either description or notes. Those belong in the storyboard and current-clip continuity. A sentence that would become false in a different scene does not belong on a character card.
- Wardrobe cards describe one reusable look and name its owner_character when fixed to a character. Do not create cards for incidental clothing with no continuity value.
- Prop cards include only handled, plot-critical, recurring or continuity-critical objects. Environments describe stable layout, architecture, palette, light sources and spatial anchors.
- Create a voice card only for a character who speaks, narrates or is explicitly expected to speak. Describe apparent age, pitch, timbre, accent/register, energy, cadence, emotional range, contrast from other voices and exclusions. Audio may later become the primary acoustic authority; this text remains the fallback and performance guardrail.
- series_voice_style defines shared language-performance, acting, recording texture, audience tone and global exclusions. It may be empty only when the production contains no speech.
- Existing user content is protected by the application. Return complete useful proposals for blank or missing material, but do not rename existing cards.
- Prefer one specific card over several near-duplicates. Do not create cards for unnamed extras unless they recur or affect continuity."""


def _text(value, name, limit, default=""):
    value = default if value is None else value
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{name} must be text with at most {limit} characters.")
    return value.strip()


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def empty_card_library():
    return {kind: [] for kind in CARD_KINDS}


def empty_overview_assets():
    return {kind: None for kind in OVERVIEW_CARD_KINDS}


def normalise_overview_assets(value):
    if value is None:
        return empty_overview_assets()
    if not isinstance(value, dict) or set(value) - set(OVERVIEW_CARD_KINDS):
        raise ValueError("overview_asset_ids contains unsupported card kinds.")
    result = empty_overview_assets()
    for kind in OVERVIEW_CARD_KINDS:
        asset_id = value.get(kind)
        if asset_id:
            safe_id(asset_id)
        result[kind] = asset_id or None
    return result


def normalise_card(value, kind, index):
    if not isinstance(value, dict):
        raise ValueError(f"Every {kind} card must be an object.")
    ident = value.get("id") or str(uuid.uuid4())
    safe_id(ident)
    asset_ids = value.get("asset_ids", [])
    if not isinstance(asset_ids, list) or len(asset_ids) > 24:
        raise ValueError("A card can contain at most 24 reference files.")
    for asset_id in asset_ids:
        safe_id(asset_id)
    locked = value.get("locked", True)
    if type(locked) is not bool:
        raise ValueError("card.locked must be true or false.")
    result = {
        "id": ident,
        "name": _text(value.get("name"), "card name", 120, f"{kind} {index + 1}") or f"{kind} {index + 1}",
        "description": _text(value.get("description"), "card description", 6000),
        "image_analysis": _text(value.get("image_analysis"), "card image analysis", 6000),
        "image_generation_prompt": _text(value.get("image_generation_prompt"), "card image generation prompt", 3000),
        "notes": _text(value.get("notes"), "card notes", 3000),
        "asset_ids": list(dict.fromkeys(asset_ids)),
        "locked": locked,
    }
    # These optional links make the cards operational while remaining portable.
    for key in ("subject_id", "owner_card_id", "character_card_id", "voice_card_id"):
        link = value.get(key)
        if link:
            safe_id(link)
        result[key] = link or None
    for key, limit in (("voice_id", 100), ("language", 80), ("pace", 160)):
        result[key] = _text(value.get(key), key, limit)
    return result


def normalise_cards(value):
    if value is None:
        return empty_card_library()
    if not isinstance(value, dict) or set(value) - set(CARD_KINDS):
        raise ValueError("cards must contain only supported card kinds.")
    result = empty_card_library()
    for kind in CARD_KINDS:
        cards = value.get(kind, [])
        if not isinstance(cards, list) or len(cards) > 64:
            raise ValueError(f"{kind} must contain at most 64 cards.")
        result[kind] = [normalise_card(card, kind, index) for index, card in enumerate(cards)]
    # A subject is one on-screen identity. Older imported card sets could assign
    # the same Studio subject ID to two differently named character cards. Drop
    # every ambiguous link here so materialisation can rebind each named card to
    # its own exact-name subject (or create one) without merging identities or
    # moving reference images between cards.
    subject_links = {}
    for card in result["characters"]:
        if card.get("subject_id"):
            subject_links.setdefault(card["subject_id"], []).append(card)
    for linked in subject_links.values():
        if len(linked) > 1:
            for card in linked:
                card["subject_id"] = None
    return result


def normalise_episode(value, index, character_ids, seen_character_ids=None):
    if not isinstance(value, dict):
        raise ValueError("Every episode must be an object.")
    ident = value.get("id") or str(uuid.uuid4())
    safe_id(ident)
    raw_ids = value.get("character_card_ids", [])
    if not isinstance(raw_ids, list) or len(raw_ids) > 64:
        raise ValueError("An episode can reference at most 64 character cards.")
    selected = []
    for card_id in raw_ids:
        if isinstance(card_id, str) and card_id in character_ids and card_id not in selected:
            selected.append(card_id)
    seen = seen_character_ids or set()
    return {
        "id": ident,
        "index": index + 1,
        "title": _text(value.get("title"), "episode title", 160, f"Episode {index + 1}") or f"Episode {index + 1}",
        "logline": _text(value.get("logline"), "episode logline", 1200),
        "story": _text(value.get("story"), "episode story", 20000),
        "character_card_ids": selected,
        "returning_character_card_ids": [card_id for card_id in selected if card_id in seen],
        "continuity_notes": _text(value.get("continuity_notes"), "episode continuity notes", 3000),
    }


def current_episode_story(production):
    episodes = production.get("episodes") or []
    current = production.get("current_episode", 1)
    # A one-episode production already has an exact source screenplay. Feeding
    # an AI-written episode synopsis into the storyboard planner used to lose
    # timecodes, interjections and exact dialogue before clip planning began.
    if int(production.get("episode_count", 1)) == 1 and production.get("brief", "").strip():
        return production["brief"]
    if episodes and 1 <= current <= len(episodes):
        return episodes[current - 1].get("story") or episodes[current - 1].get("logline") or production["brief"]
    return production["brief"]


_TIMECODE_RANGE = re.compile(
    r"(?m)^[ \t]*(?P<start_min>\d{1,3}):(?P<start_sec>[0-5]\d)[ \t]*"
    r"[-–—~～][ \t]*(?P<end_min>\d{1,3}):(?P<end_sec>[0-5]\d)[ \t]*$"
)

# Prefer time ranges attached to explicit shot headings when they exist. A
# screenplay often also contains broader act/section ranges; treating those as
# shots pulls the preamble, cast list and production notes into the first clip.
_SHOT_TIMECODE_RANGE = re.compile(
    r"(?im)^[ \t]*(?:[*#]+[ \t]*)?(?:分镜|分鏡|镜头|鏡頭|shot)[ \t]*"
    r"[A-Za-z0-9一二三四五六七八九十._-]*[ \t]*(?:[|｜:：][ \t]*)?"
    r"(?P<start_min>\d{1,3}):(?P<start_sec>[0-5]\d)[ \t]*"
    r"[-–—~～][ \t]*(?P<end_min>\d{1,3}):(?P<end_sec>[0-5]\d)[^\r\n]*$"
)


def timed_story_beats(story):
    """Read explicit screenplay time ranges without interpreting prose."""
    if not isinstance(story, str):
        return []
    shot_matches = list(_SHOT_TIMECODE_RANGE.finditer(story))
    matches = shot_matches or list(_TIMECODE_RANGE.finditer(story))
    beats = []
    previous_end = -1
    for index, match in enumerate(matches):
        start = int(match.group("start_min")) * 60 + int(match.group("start_sec"))
        end = int(match.group("end_min")) * 60 + int(match.group("end_sec"))
        if end <= start or start < previous_end:
            return []
        text_end = matches[index + 1].start() if index + 1 < len(matches) else len(story)
        body = story[match.end():text_end].strip()
        if not body:
            return []
        beats.append({"start": start, "end": end, "duration": end - start, "text": body})
        previous_end = end
    return beats


def timed_clip_groups(story):
    """Merge only sub-five-second authored beats into an adjacent H3 clip."""
    groups = copy.deepcopy(timed_story_beats(story))
    if not groups:
        return []
    # A source beat longer than H3's maximum needs semantic AI splitting, so it
    # is not safe to advertise a deterministic group map for that screenplay.
    if any(group["duration"] > MAX_SECONDS for group in groups):
        return []
    while True:
        short = next((index for index, group in enumerate(groups)
                      if group["duration"] < PLANNED_MIN_SECONDS), None)
        if short is None:
            break
        candidates = []
        if short > 0:
            total = groups[short - 1]["duration"] + groups[short]["duration"]
            if total <= MAX_SECONDS:
                candidates.append((abs(total - DEFAULT_CLIP_SECONDS), 0, short - 1, short))
        if short + 1 < len(groups):
            total = groups[short]["duration"] + groups[short + 1]["duration"]
            if total <= MAX_SECONDS:
                candidates.append((abs(total - DEFAULT_CLIP_SECONDS), 1, short, short + 1))
        if not candidates:
            return []
        _distance, _prefer_previous, left, right = min(candidates)
        merged = {
            "start": groups[left]["start"], "end": groups[right]["end"],
            "duration": groups[right]["end"] - groups[left]["start"],
            "text": groups[left]["text"].rstrip() + "\n\n" + groups[right]["text"].lstrip(),
        }
        groups[left:right + 1] = [merged]
    return groups


def _authored_action_cue(text, character_names):
    """Remove quoted speech while retaining authored physical action."""
    names = {name.strip().casefold() for name in character_names if name.strip()}
    rows = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line[0] in "“‘\"'":
            continue
        marker = re.match(r"^([^:：\n]{1,160}?)[：:]\s*(.*)$", line)
        if marker:
            lead, tail = marker.group(1).strip(), marker.group(2).strip()
            speaker = re.sub(r"(?:继续|continues?)\s*$", "", lead, flags=re.I).strip().casefold()
            exact_speaker = speaker in names or collective_speaker(speaker)
            if exact_speaker:
                if tail and tail[0] not in "“‘\"'":
                    rows.append(tail)
                continue
            line = lead + ((" " + tail) if tail and tail[0] not in "“‘\"'" else "")
        rows.append(line.rstrip("：:").strip())
    return " ".join(dict.fromkeys(row for row in rows if row))


def authored_internal_timing(production, segment):
    """Preserve authored sub-beat timing inside a merged 5–15 second clip.

    Dialogue stays in the structured dialogue roster. Only physical/narrative
    cues are repeated here, so H3 receives useful local phase boundaries without
    duplicating or paraphrasing the exact spoken lines.
    """
    story = current_episode_story(production)
    groups, beats = timed_clip_groups(story), timed_story_beats(story)
    index = int(segment.get("index", 0)) - 1
    if not (0 <= index < len(groups)):
        return ""
    group = groups[index]
    # Segment order is not proof that an AI-edited storyboard still describes
    # this source beat. Only carry sub-beat directions when the authored group
    # is actually present in the segment's own text. A translated/reimagined
    # segment simply uses its structured action and duration instead.
    segment_text = "\n".join(str(segment.get(key, "")) for key in ("story", "action"))
    if not segment_text or group["text"].strip() not in segment_text:
        return ""
    contained = [beat for beat in beats
                 if beat["start"] >= group["start"] and beat["end"] <= group["end"]]
    if len(contained) <= 1 or group["duration"] <= 0:
        return ""
    scale = float(segment.get("duration", group["duration"])) / group["duration"]
    names = [card.get("name", "") for card in production.get("cards", {}).get("characters", [])]
    rows = []
    for number, beat in enumerate(contained, 1):
        start = (beat["start"] - group["start"]) * scale
        end = (beat["end"] - group["start"]) * scale
        cue = _authored_action_cue(beat["text"], names) or f"complete authored beat {number}"
        rows.append(f"- {start:.2f}-{end:.2f}s: {cue}")
    return "\n".join([
        "AUTHORED INTERNAL TIMING — relative to this one continuous clip:",
        *rows,
        "Preserve these phase boundaries and source order. Keep exact spoken words only in the structured dialogue cues; do not copy dialogue into action.",
    ])


_DIALOGUE_OPENERS = "“‘\"'「『"


def _strip_script_markdown(value):
    """Remove screenplay Markdown around a cue without touching spoken words."""
    value = str(value or "").strip()
    value = re.sub(r"^(?:>\s*)+", "", value).strip()
    value = re.sub(r"^(?:#{1,6}\s+|[-+]\s+)", "", value).strip()
    changed = True
    while changed:
        changed = False
        for token in ("**", "__", "*", "_"):
            if len(value) > len(token) * 2 and value.startswith(token) and value.endswith(token):
                value = value[len(token):-len(token)].strip()
                changed = True
                break
    return value


def _clean_dialogue_value(value):
    value = _strip_script_markdown(value)
    pairs = {"“": "”", "‘": "’", '"': '"', "'": "'", "「": "」", "『": "』"}
    if len(value) >= 2 and value[0] in pairs and value[-1] == pairs[value[0]]:
        value = value[1:-1].strip()
    return value


def _speaker_marker(value):
    """Read plain or Markdown speaker cues and discard acting direction."""
    value = _strip_script_markdown(value)
    marker = re.match(
        r"^(?:\*\*|__|[*_])?(?P<label>[^:：\n]{1,120}?)[：:]"
        r"(?:\*\*|__|[*_])?\s*(?P<inline>.*)$", value, re.I)
    if not marker:
        return None
    label = re.sub(r"(?:继续|continues?)\s*$", "", marker.group("label").strip(), flags=re.I).strip()
    # Common screenplay forms put performance after a vertical bar or in a
    # trailing bracket: **Pokke｜quietly：** / Pokke (quietly):
    speaker = re.split(r"[|｜]", label, maxsplit=1)[0].strip()
    speaker = re.sub(r"\s*(?:\([^()]{1,100}\)|（[^（）]{1,100}）|\[[^\[\]]{1,100}\]|【[^【】]{1,100}】)\s*$", "", speaker).strip()
    speaker = speaker.strip("*_#- ")
    return (speaker, marker.group("inline").strip()) if speaker else None


def _dialogue_signal_count(text):
    """Count strong cue+quote signals independently from the main parser."""
    rows = [raw.strip() for raw in str(text or "").splitlines() if raw.strip()]
    count = 0
    for index, raw in enumerate(rows):
        marker = _speaker_marker(raw)
        if not marker:
            continue
        _speaker, inline = marker
        candidate = _strip_script_markdown(inline)
        if candidate and candidate[0] in _DIALOGUE_OPENERS:
            count += 1
            continue
        if index + 1 < len(rows):
            candidate = _strip_script_markdown(rows[index + 1])
            if candidate and candidate[0] in _DIALOGUE_OPENERS:
                count += 1
    return count


def script_dialogue(text):
    """Extract quoted dialogue from plain text and common Markdown scripts."""
    result, pending = [], None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        marker = _speaker_marker(line)
        if marker:
            speaker, inline = marker
            pending = speaker
            inline = _strip_script_markdown(inline)
            if inline and inline[0] in _DIALOGUE_OPENERS:
                value = _clean_dialogue_value(inline)
                if value:
                    result.append({"speaker": speaker, "text": value})
            continue
        candidate = _strip_script_markdown(line)
        if pending and candidate and candidate[0] in _DIALOGUE_OPENERS:
            value = _clean_dialogue_value(candidate)
            if value:
                result.append({"speaker": pending, "text": value})
        elif pending:
            pending = None
    return result


def _dialogue_already_matches_language(text, language):
    has_latin = bool(re.search(r"[A-Za-z]", text))
    has_han = bool(re.search(r"[\u3400-\u9fff]", text))
    has_kana = bool(re.search(r"[\u3040-\u30ff]", text))
    if language == "en":
        return has_latin and not has_han and not has_kana
    if language == "ja":
        return has_kana or (has_han and not has_latin)
    return has_han and not has_kana


def locked_timed_dialogue(production, story=None):
    """Return source-exact lines when no language translation is necessary."""
    story = story if story is not None else current_episode_story(production)
    cards = {card["name"].strip().casefold(): card["name"]
             for card in production.get("cards", {}).get("characters", [])}
    collective = {"en": "All", "ja": "みんな", "zh-CN": "所有人", "zh-TW": "所有人"}
    groups = []
    for index, group in enumerate(timed_clip_groups(story), 1):
        lines, source_lines, requires_translation = [], [], False
        parsed = script_dialogue(group["text"])
        for line in parsed:
            speaker = line["speaker"]
            if collective_speaker(speaker):
                speaker = collective[production["language"]]
            else:
                folded = speaker.casefold()
                speaker = cards.get(folded, speaker)
                if speaker == line["speaker"]:
                    prefixed = [name for key, name in cards.items() if folded.startswith(key)]
                    if len(prefixed) == 1:
                        # Screenplays often write an action cue such as
                        # "Pokke pops out of the cushions:" before the quote.
                        speaker = prefixed[0]
            row = {"speaker": speaker, "text": line["text"],
                   "language": PRODUCTION_LANGUAGES[production["language"]],
                   "voiceover": False}
            source_lines.append(copy.deepcopy(row))
            if _dialogue_already_matches_language(line["text"], production["language"]):
                lines.append(copy.deepcopy(row))
            else:
                requires_translation = True
        groups.append({"clip": index, "start_seconds": group["start"],
                       "end_seconds": group["end"], "dialogue": lines,
                       "source_dialogue": source_lines,
                       "requires_translation": requires_translation,
                       "dialogue_parse_failed": bool(_dialogue_signal_count(group["text"]) and not parsed)})
    return groups


def production_schema_for_story(story):
    """Preserve source groups while allowing dense groups to split safely."""
    schema = copy.deepcopy(PRODUCTION_SCHEMA)
    groups = timed_clip_groups(story)
    count = len(groups)
    if count:
        items = schema["properties"]["segments"]
        items["minItems"] = count
        items["maxItems"] = min(
            MAX_SEGMENTS,
            sum(max(1, group["duration"] // PLANNED_MIN_SECONDS) for group in groups),
        )
    return schema


def recommended_clip_count(target_seconds):
    """Use ten seconds as a baseline while keeping every new clip within 5-15s."""
    target = max(PLANNED_MIN_SECONDS, int(round(target_seconds)))
    minimum = max(1, math.ceil(target / MAX_SECONDS))
    maximum = max(minimum, target // PLANNED_MIN_SECONDS)
    preferred = max(1, math.floor(target / DEFAULT_CLIP_SECONDS + 0.5))
    return min(max(preferred, minimum), maximum, MAX_SEGMENTS)


def episode_timing_targets(production, chunk_index=1, chunk_total=1, story=None):
    """Return auditable whole-episode and per-planning-part timing constraints."""
    whole = max(PLANNED_MIN_SECONDS, int(round(float(production.get("episode_minutes", 8)) * 60)))
    chunk_total = max(1, int(chunk_total))
    chunk_index = min(max(1, int(chunk_index)), chunk_total)
    base, remainder = divmod(whole, chunk_total)
    part = base + (1 if chunk_index <= remainder else 0)
    part = max(PLANNED_MIN_SECONDS, part)
    minimum = max(1, math.ceil(part / MAX_SECONDS))
    maximum = max(minimum, part // PLANNED_MIN_SECONDS)
    source_story = story if story is not None else current_episode_story(production)
    timed_beats = timed_story_beats(source_story)
    timed_groups = timed_clip_groups(source_story)
    recommended = recommended_clip_count(part)
    if chunk_total == 1 and timed_groups and minimum <= len(timed_groups) <= min(maximum, MAX_SEGMENTS):
        recommended = len(timed_groups)
    return {
        "episode_target_seconds": whole,
        "part_target_seconds": part,
        "default_clip_seconds": DEFAULT_CLIP_SECONDS,
        "recommended_clip_count": recommended,
        "feasible_clip_count": {"minimum": minimum, "maximum": min(maximum, MAX_SEGMENTS)},
        "clip_duration_seconds": {"minimum": PLANNED_MIN_SECONDS, "maximum": MAX_SECONDS},
        "source_timed_beat_count": len(timed_beats),
        "timed_clip_groups": [
            {"clip": index + 1, "start_seconds": group["start"],
             "end_seconds": group["end"], "duration_seconds": group["duration"]}
            for index, group in enumerate(timed_groups)
        ] if chunk_total == 1 else [],
    }


def fallback_episodes(production):
    """Deterministic offline episode plan with card-name cast detection."""
    story = production["brief"].strip()
    if not story:
        raise ValueError("Write a source story or screenplay before planning episodes.")
    count = production["episode_count"]
    units = [x.strip() for x in re.split(r"(?<=[。！？!?])\s*|\n{2,}", story) if x.strip()]
    if len(units) < count:
        width = max(1, math.ceil(len(story) / count))
        units = [story[i:i + width].strip() for i in range(0, len(story), width) if story[i:i + width].strip()]
    groups = [[] for _ in range(count)]
    lengths = [0] * count
    cursor = 0
    remaining = sum(len(x) for x in units)
    for unit in units:
        target = max(1, math.ceil(remaining / max(1, count - cursor)))
        if cursor < count - 1 and lengths[cursor] and lengths[cursor] + len(unit) > target:
            cursor += 1
        groups[cursor].append(unit)
        lengths[cursor] += len(unit)
        remaining -= len(unit)
    language = production["language"]
    prefixes = {"en": "Episode", "ja": "第", "zh-CN": "第", "zh-TW": "第"}
    suffixes = {"en": "", "ja": "話", "zh-CN": "集", "zh-TW": "集"}
    cards = production["cards"]["characters"]
    result = []
    for index, group in enumerate(groups):
        text = "\n".join(group).strip()
        names = [card["name"] for card in cards if card["name"] and card["name"].casefold() in text.casefold()]
        result.append({
            "title": f"{prefixes[language]} {index + 1}{suffixes[language]}",
            "logline": text[:240], "story": text,
            "character_names": names, "continuity_notes": "",
        })
    return result


def cards_from_project(project):
    """Lift existing H3 references into a production-level reusable card library."""
    cards = empty_card_library()
    subject_cards, subject_for_asset = {}, {}
    for subject in project.get("subjects", []):
        card_id = str(uuid.uuid4())
        subject_cards[subject["id"]] = card_id
        for asset_id in subject.get("asset_ids", []):
            subject_for_asset[asset_id] = subject["id"]
        cards["characters"].append(normalise_card({
            "id": card_id, "name": subject["name"], "description": subject.get("description", ""),
            "subject_id": subject["id"], "asset_ids": [], "locked": True,
        }, "characters", len(cards["characters"])))
    character_by_id = {card["subject_id"]: card for card in cards["characters"]}
    for asset in project.get("assets", []):
        asset_id = asset.get("id")
        if not asset_id:
            continue
        owner_subject = asset.get("simple_owner_id") or subject_for_asset.get(asset_id)
        owner_card = subject_cards.get(owner_subject)
        media_type = asset.get("media_type")
        semantic = asset.get("semantic_role", "other")
        if media_type == "audio":
            cards["voices"].append(normalise_card({
                "name": asset.get("name") or "Voice reference", "description": asset.get("description", ""),
                "asset_ids": [asset_id], "character_card_id": owner_card,
                "voice_id": (asset.get("prompt_tag") or asset.get("name") or "VOICE")[:100],
            }, "voices", len(cards["voices"])))
        elif media_type == "image" and semantic in ("face", "character") and owner_subject in character_by_id:
            character_by_id[owner_subject]["asset_ids"].append(asset_id)
        elif media_type == "image":
            kind = {"wardrobe": "wardrobe", "object": "props", "background": "environments",
                    "style": "styles", "palette": "styles"}.get(semantic)
            if kind:
                cards[kind].append(normalise_card({
                    "name": asset.get("name") or semantic.title(), "description": asset.get("description", ""),
                    "asset_ids": [asset_id], "owner_card_id": owner_card,
                }, kind, len(cards[kind])))
    return cards


def production_context_hash(production):
    context = {key: production.get(key) for key in
        ("title", "language", "visual_style_preset", "visual_style_custom", "narrative_style", "narrative_style_custom", "narrative_notes",
         "style_bible", "character_bible", "continuity_notes", "series_voice_style", "cards", "overview_asset_ids",
         "video_aspect_ratio", "video_resolution", "video_quality", "video_steps",
         "current_episode", "episodes")}
    # Prompt-renderer behavior is part of the prepared clip contract. Bump this
    # when a correction requires existing saved prompts to be regenerated;
    # source projects, cards, media and completed videos remain untouched.
    context["production_prompt_renderer"] = "temporal-cast-continuity-v6"
    if production.get("prompt_version", "classic") != "classic":
        context["prompt_version"] = production["prompt_version"]
    if production.get("prompt_version") == "continuity_director":
        # This existing UI choice now renders the source-bound storyboard.
        # Mark older prepared Director clips stale without changing their data.
        context["prompt_renderer"] = "storyboard_narrative_v1"
    return _hash(context)


def card_plan_source_hash(production):
    """Version AI-derived text cards by story and high-level creative intent."""
    return _hash({key: production.get(key) for key in (
        "brief", "language", "style_bible", "character_bible",
        "visual_style_preset", "visual_style_custom", "narrative_style",
        "narrative_style_custom", "narrative_notes",
    )})


def has_substantive_card_library(production):
    """Detect hand-authored text; reference media alone still benefits from AI text."""
    if production.get("series_voice_style"):
        return True
    return any(
        card.get("description") or card.get("notes")
        or (kind == "voices" and (card.get("voice_id") or card.get("pace")))
        for kind in CARD_KINDS
        for card in production.get("cards", {}).get(kind, [])
    )


def normalise_card_selection(value):
    if value is None:
        return {kind: [] for kind in SELECTABLE_CARD_KINDS}
    if not isinstance(value, dict) or set(value) - set(SELECTABLE_CARD_KINDS):
        raise ValueError("card_selection contains unsupported card kinds.")
    result = {}
    for kind in SELECTABLE_CARD_KINDS:
        names = value.get(kind, [])
        limit = 3 if kind == "voices" else 16
        if not isinstance(names, list) or len(names) > limit:
            raise ValueError(f"card_selection.{kind} contains too many cards.")
        result[kind] = list(dict.fromkeys(
            _text(name, f"card_selection.{kind}", 120) for name in names if isinstance(name, str) and name.strip()))
    return result


def normalise_cast_timeline(value, fallback_characters=()):
    """Return a backward-compatible, contradiction-free temporal cast map.

    Older productions only stored card_selection.characters. Treat those names
    as visible at both boundaries until the storyboard is replanned; new plans
    provide explicit entrances, exits and off-screen-only mentions.
    """
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(CAST_TIMELINE_KEYS):
        raise ValueError("cast_timeline contains unsupported fields.")
    result = {}
    for key in CAST_TIMELINE_KEYS:
        names = value.get(key, [])
        if not isinstance(names, list) or len(names) > 16:
            raise ValueError(f"cast_timeline.{key} contains too many characters.")
        result[key] = list(dict.fromkeys(
            _text(name, f"cast_timeline.{key}", 120)
            for name in names if isinstance(name, str) and name.strip()))
    if not any(result.values()):
        fallback = list(dict.fromkeys(
            _text(name, "cast_timeline fallback", 120)
            for name in fallback_characters if isinstance(name, str) and name.strip()))[:16]
        result["visible_start"] = list(fallback)
        result["visible_end"] = list(fallback)
    visible = set(result["visible_start"] + result["visible_end"] + result["enters"] + result["exits"])
    result["offscreen"] = [name for name in result["offscreen"] if name not in visible]
    excluded = visible | set(result["offscreen"])
    result["mentioned_only"] = [name for name in result["mentioned_only"] if name not in excluded]
    return result


def temporal_cast_lock(segment):
    """Render segment state as a deterministic prompt constraint."""
    timeline = normalise_cast_timeline(
        segment.get("cast_timeline"), segment.get("card_selection", {}).get("characters", []))
    labels = (
        ("Visible at opening", "visible_start"),
        ("Enter physically during this clip", "enters"),
        ("Exit physically during this clip", "exits"),
        ("Visible in the final frame", "visible_end"),
        ("Off-screen for the entire clip", "offscreen"),
        ("Mentioned only; never depict", "mentioned_only"),
    )
    rows = [f"{label}: {', '.join(timeline[key]) or 'none'}." for label, key in labels]
    rows.extend([
        "Opening visibility and final-frame visibility are different contracts; an exiting identity must not remain in the final frame.",
        "Off-screen and mentioned-only identities must not appear as people, creatures, background figures, reflections, portraits, duplicates or disguised substitutes.",
        f"Editorial transition from the preceding clip: {segment.get('transition_mode', 'hard_cut')}.",
    ])
    return "TEMPORAL CAST AND STATE LOCK — CURRENT CLIP\n" + "\n".join(rows)


_PERSISTENT_DEPARTURE_CUE = re.compile(
    r"(?:\bleav(?:e|es|ing|eft)\b|\bdepart(?:s|ed|ing)?\b|\bexit(?:s|ed|ing)?\b|"
    r"\bteleport(?:s|ed|ing)?\b|\btransport(?:s|ed|ing)?\b|\bretreat(?:s|ed|ing)?\b|"
    r"\bpull(?:s|ed|ing)?\s+back\b|\bsent\s+(?:away|back|home|below)\b|\bremoved\s+from\b|"
    r"\breset(?:s|ting)?\s+(?:away|back|below)\b|离开|退出|离场|离去|走出|被送回|传送|退場|立ち去|離れ|転送)",
    re.IGNORECASE)


def persistent_story_departure(name, segment, transition_mode):
    """Return whether an ``exits`` entry should carry into later clips.

    Small local models sometimes use ``exits`` for leaving the current camera
    composition (walking through a gate, climbing out of frame, or a dissolve).
    That is useful for the current final-frame lock but must not permanently
    ban the same performer from the next adjoining shot. State changes are
    always persistent; ordinary cuts require an explicit departure cue close
    to the named character.
    """
    if transition_mode == "state_change":
        return True
    text = "\n".join(str(segment.get(key, "")) for key in
                     ("story", "action", "ending")).casefold()
    folded = str(name or "").strip().casefold()
    if not folded or not text:
        return False
    aliases = [folded]
    words = re.findall(r"[a-z0-9]+", folded)
    if len(words) > 1 and len(words[-1]) >= 4:
        aliases.append(words[-1])
    for alias in dict.fromkeys(aliases):
        start = 0
        while True:
            found = text.find(alias, start)
            if found < 0:
                break
            window = text[max(0, found - 100):min(len(text), found + len(alias) + 140)]
            if _PERSISTENT_DEPARTURE_CUE.search(window):
                return True
            start = found + len(alias)
    return False


_CLIP_ONLY_CHARACTER_FACT = re.compile(
    r"\b(?:currently|previous(?:ly)?|earlier|"
    r"this (?:scene|clip|episode)|of the scene|scene's|state of transition|"
    r"attempts?|observes?|notices?|chooses?|provides?|participates?|"
    r"protects?|waits for|reacts to|possesses|time jump)\b|"
    r"当前|此时|本(?:场|段|镜头|集)|上一(?:场|段|镜头|集)|正在|刚刚|剧情中|"
    r"現在|この(?:場面|シーン|カット|話)|前の(?:場面|シーン|カット)|直前",
    re.IGNORECASE,
)
_IDENTITY_HEADING = re.compile(
    r"^(?:appearance|signature details(?:\s*/\s*accessories)?|visual identity|"
    r"外观|外貌|标志特征|配饰|見た目|外見|特徴)\s*[:：]$",
    re.IGNORECASE,
)


def render_character_identity(card):
    """Keep reusable appearance, not an old clip's acting, in the H3 Subject.

    The original card and its planning context remain intact. This conservative
    line filter only changes the detached render copy; it never rewrites a
    user's card or infers visual facts from an image.
    """
    lines = []
    seen = set()
    for raw in (card.get("description", "") + "\n" + card.get("notes", "")).splitlines():
        line = raw.strip()
        if not line or _IDENTITY_HEADING.fullmatch(line):
            continue
        for sentence in re.split(r"(?<=[.!?。！？])\s+", line):
            sentence = sentence.strip()
            key = re.sub(r"\s+", " ", sentence).casefold()
            if (sentence and key not in seen and
                    not _CLIP_ONLY_CHARACTER_FACT.search(sentence)):
                lines.append(sentence)
                seen.add(key)
    return " ".join(lines)


def character_is_embedded_form(card, text):
    """Return true when a character is explicitly present only as an object.

    A badge/emblem state belongs to its owner visually; compiling it as another
    physical Subject creates a contradictory head count and encourages H3 to
    duplicate bodies in ensemble shots.
    """
    name = str(card.get("name", "")).strip().casefold()
    haystack = str(text or "").casefold()
    if not name or len(name) < 2 or name not in haystack:
        return False
    forms = (
        "badge form", "as a badge", "remains a badge", "static badge", "badge on",
        "emblem form", "as an emblem", "徽章形态", "徽章形態", "徽章形式",
        "バッジ形態", "バッジの姿", "バッジとして",
    )
    name_pattern = (r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
                    if re.search(r"[a-z0-9]", name) else re.escape(name))
    for match in re.finditer(name_pattern, haystack):
        # The state must follow this exact name closely. A broad window would
        # make a short character name such as "A" inherit another character's
        # badge state later in the sentence.
        following = haystack[match.end():min(len(haystack), match.end() + 96)]
        following = re.split(r"[.;。；!?！？\n]", following, maxsplit=1)[0]
        if any(form in following for form in forms):
            return True
    return False


_CAST_LINE = re.compile(
    r"(?im)^[ \t]*(?:[*#]+[ \t]*)?(?:本组出场|本組出場|本集出场|本集出場|"
    r"出场角色(?:表)?|出場角色(?:表)?|cast|characters)[ \t]*[:：][ \t]*(?P<names>[^\r\n]+)$"
)


def _cast_token_parts(value):
    """Return the main cast label plus possible parenthesised aliases."""
    def clean(part):
        return str(part or "").strip().strip("*#- ").strip(".。!！?？;；:：、，, ")

    raw = clean(value)
    if not raw:
        return []
    parts = []
    for inside in re.findall(r"[（(]([^()（）]+)[）)]", raw):
        parts.append(clean(inside))
    base = clean(re.sub(r"[（(][^()（）]+[）)]", "", raw))
    if base:
        parts.insert(0, base)
    return list(dict.fromkeys(part for part in parts if part))


def _name_occurs(alias, text):
    alias = str(alias or "").strip().casefold()
    haystack = str(text or "").casefold()
    if not alias:
        return False
    if re.search(r"[a-z0-9]", alias):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", haystack))
    return alias in haystack


def character_aliases(production, story=None):
    """Map screenplay-local cast labels to canonical character cards.

    Card names deliberately remain stable and reusable across projects, while a
    screenplay may use a translated display name. When an authored cast line
    and the episode's explicit character-card list have the same length, their
    order is an unambiguous local binding. Exact names still win, and aliases
    never modify the card library.
    """
    characters = production.get("cards", {}).get("characters", [])
    aliases = {card["id"]: {card.get("name", "").strip().casefold()}
               for card in characters if card.get("id")}
    story = str(story if story is not None else current_episode_story(production) or "")
    matches = list(_CAST_LINE.finditer(story))
    if not matches:
        return aliases
    tokens = [token.strip() for token in re.split(r"[、,，;；]", matches[0].group("names")) if token.strip()]
    current = int(production.get("current_episode", 1) or 1)
    episodes = production.get("episodes") or []
    episode = episodes[current - 1] if 1 <= current <= len(episodes) else {}
    by_id = {card["id"]: card for card in characters}
    ordered = [by_id[card_id] for card_id in episode.get("character_card_ids", []) if card_id in by_id]

    # First bind tokens that visibly contain an exact canonical card name.
    token_owner = {}
    for token_index, token in enumerate(tokens):
        for card in characters:
            if _name_occurs(card.get("name"), token):
                token_owner[token_index] = card
                break
    # A planned episode stores its cast in the same order as the authored cast
    # line. Use that pairing only when both complete lists agree in length.
    if ordered and len(ordered) == len(tokens):
        for index, card in enumerate(ordered):
            token_owner.setdefault(index, card)
    for index, card in token_owner.items():
        parts = _cast_token_parts(tokens[index])
        for part_index, part in enumerate(parts):
            # Parentheses frequently describe state rather than a second name
            # ("Courage Star (badge form)"). Keep the main label; accept a
            # parenthesised value only when it visibly contains the canonical
            # name, or when a CJK canonical name supplies a Latin translation.
            if (part_index > 0 and not _name_occurs(card.get("name"), part) and not
                    (re.search(r"[\u3040-\u30ff\u3400-\u9fff]", card.get("name", "")) and
                     re.search(r"[a-z]", part, re.I))):
                continue
            aliases.setdefault(card["id"], set()).add(part.casefold())

    # Scripts often introduce a translated full role name in the cast line and
    # then use its distinctive CJK suffix (e.g. 发条维修守卫 -> 守卫). Add only
    # suffixes unique within this production so a generic role cannot bind two
    # different characters.
    suffix_owners = {}
    for card_id, names in aliases.items():
        for name in names:
            if re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff]{4,}", name):
                for size in range(2, min(5, len(name)) + 1):
                    suffix_owners.setdefault(name[-size:], set()).add(card_id)
    for suffix, owners in suffix_owners.items():
        if len(owners) == 1:
            aliases[next(iter(owners))].add(suffix)
    return aliases


def canonicalise_character_mentions(value, production, cards):
    """Render selected screenplay aliases with their canonical card names.

    The saved screenplay and reusable cards remain untouched.  This is a
    delivery-only pass: it prevents the language translator from turning a
    local role label such as 发条维修守卫 into a second English name while the
    Subject and voice card use The Clockwork Keeper.
    """
    text = str(value or "")
    if not text or not cards:
        return text
    aliases = character_aliases(production)
    replacements = []
    for card in cards:
        canonical = str(card.get("name", "")).strip()
        if not canonical:
            continue
        for alias in aliases.get(card.get("id"), set()):
            alias = str(alias or "").strip()
            if not alias or alias.casefold() == canonical.casefold():
                continue
            replacements.append((alias, canonical))
    # Replace distinctive full aliases before unique short CJK suffixes.
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for alias, canonical in replacements:
        if re.search(r"[a-z0-9]", alias, re.I):
            pattern = r"(?<![\w])" + re.escape(alias) + r"(?![\w])"
        else:
            pattern = re.escape(alias)
        text = re.sub(pattern, lambda _match, name=canonical: name,
                      text, flags=re.IGNORECASE)
    return text


def card_context(production, selected_cards=None, *, include_characters=True):
    labels = {"characters": "CHARACTER CARDS", "wardrobe": "WARDROBE CARDS",
              "props": "PROP CARDS", "environments": "ENVIRONMENT CARDS",
              "voices": "VOICE CARDS", "styles": "STYLE CARDS"}
    character_names = {card["id"]: card["name"] for card in production["cards"]["characters"]}
    groups = []
    for kind in CARD_KINDS:
        if kind == "voices":
            continue  # Only speaking-character voice cards belong in a clip prompt.
        if kind == "characters" and not include_characters:
            continue  # Subjects already carry the exact selected character cards.
        rows = []
        cards = (selected_cards.get(kind, []) if selected_cards is not None
                 else production["cards"][kind])
        for card in cards:
            details = ([f"AI style-image analysis (highest visual priority): {card['image_analysis']}" ] if kind == "styles" and card.get("image_analysis") else []) + [card["description"], card["notes"]]
            if kind in ("wardrobe", "props") and card.get("owner_card_id"):
                details.append("Owner: " + character_names.get(card["owner_card_id"], "linked character"))
            if kind == "voices":
                details.extend(x for x in [
                    "Character: " + character_names.get(card.get("character_card_id"), "unassigned") if card.get("character_card_id") else "",
                    "Voice ID: " + card["voice_id"] if card["voice_id"] else "",
                    "Language: " + card["language"] if card["language"] else "",
                    "Pace: " + card["pace"] if card["pace"] else "",
                ] if x)
            rows.append(f"- {card['name']} (locked={str(card['locked']).lower()}): " + "; ".join(x for x in details if x))
        if rows:
            groups.append(labels[kind] + "\n" + "\n".join(rows))
    overview_rows = [f"- {labels[kind]}: user-supplied overview available"
                     for kind, asset_id in production.get("overview_asset_ids", {}).items()
                     if asset_id and (selected_cards is None or selected_cards.get(kind))]
    if overview_rows:
        groups.append("CATEGORY OVERVIEW IMAGES\n" + "\n".join(overview_rows))
    return "\n\n".join(groups)


def scoped_character_bible(production, cards):
    """Keep a clip's identity text limited to its selected cast."""
    if not cards:
        return ""
    names = [card["name"].strip() for card in cards if card.get("name", "").strip()]
    bible = production.get("character_bible", "").strip()
    if not bible:
        return ""
    # Prefer complete Markdown character sections. The previous line matcher
    # emitted bare headings and could pull an unselected character's paragraph
    # merely because it mentioned a selected name (for example Mimi's badge).
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", bible))
    sections = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        if heading.casefold() not in {name.casefold() for name in names}:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(bible)
        sections.append(bible[match.start():end].strip())
    if sections:
        return "\n\n".join(sections)
    selected_lines = []
    for line in bible.splitlines():
        folded = line.casefold()
        if any(
            (name.casefold() in folded if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", name)
             else re.search(rf"(?<![\w]){re.escape(name.casefold())}(?![\w])", folded))
            for name in names
        ):
            selected_lines.append(line.strip())
    return "\n".join(dict.fromkeys(line for line in selected_lines if line))


def collective_speaker(value):
    """Recognise an authored chorus label without inventing a character card."""
    if not isinstance(value, str):
        return False
    key = re.sub(r"[\s._-]+", "", value).casefold()
    return key in {"all", "everyone", "everybody", "ensemble", "chorus",
                   "众人", "大家", "全员", "所有人", "眾人", "全員", "みんな"}


def voice_prompt_context(production, cards):
    """Build one non-rewritten block for the current clip's audible cast."""
    if not cards:
        return ""
    from .narrative_prompt import _brief_series_style
    audio_cards = [card["name"] for card in cards if card.get("asset_ids")]
    sections = [
        "VOICE DIRECTION — CURRENT CLIP ONLY",
        "Use the series voice style below only to perform the explicitly tagged scripted dialogue in this clip. All other prose is silent production direction and must never be spoken, narrated or sung. Include only the listed speaking-character cards. Keep each voice identity stable and do not replace a named card with a generic age or gender description.",
        "Project dialogue language: " + PRODUCTION_LANGUAGES[production["language"]] + ". This project setting overrides any conflicting language named inside saved voice text; preserve its acting, timbre and identity traits.",
        ("VOICE AUTHORITY: Uploaded clean voice audio is the primary authority for audible identity—timbre, pitch, accent, apparent age, cadence, pace and vocal texture—for: "
         + ", ".join(audio_cards)
         + ". Written voice text supplements the audio with acting intent, emotional limits and exclusions; it must not flatten or replace clearly audible traits. Never copy the sample's words: use the exact scripted dialogue in the project language."
         if audio_cards else
         "VOICE AUTHORITY: No voice audio is uploaded for this clip. Use the written series style and speaking-character voice cards only as authority for how the scripted dialogue sounds; these descriptions are never additional words to say."),
    ]
    global_style = _brief_series_style(production.get("series_voice_style"))
    if global_style:
        # A saved series style may include an all-character voice roster. That
        # roster is not this clip's cast and must not leak extra names into the
        # classic H3 visual prompt. Selected voice cards below stay verbatim.
        sections.append("Series Voice Style:\n" + global_style)
    for card in cards:
        exact = "\n".join(part for part in (card.get("description", ""), card.get("notes", "")) if part)
        metadata = "; ".join(part for part in (
            "Voice ID: " + card.get("voice_id", "") if card.get("voice_id") else "",
            "Target language: " + PRODUCTION_LANGUAGES[production["language"]],
            "Pace / rhythm: " + card.get("pace", "") if card.get("pace") else "",
        ) if part)
        supplement = ""
        if not card.get("asset_ids"):
            continuity_key = card.get("voice_id") or card["name"]
            supplement = ("\nVoice continuity supplement: stable identity key " + continuity_key
                          + ". Preserve one recurring speaker identity across clips—consistent timbre, pitch range, "
                            "apparent age, accent, cadence and vocal texture. Keep clean close dialogue, clear diction "
                            "and natural emotional variation. Do not substitute another character's voice or generic "
                            "text-to-speech. This supplement adds continuity only and does not replace or rewrite the "
                            "verbatim card above.")
        sections.append("Voice Card — " + card["name"] + ":\n" + (exact or "Use the named stable voice identity.")
                        + ("\n" + metadata if metadata else "") + supplement)
    return "\n\n".join(sections)


def continuity_visual_lock(production, selected_cards):
    """Choose one renderable style authority for either prompt version."""
    styles = selected_cards.get("styles", [])
    analysed = next((card for card in styles if card.get("image_analysis", "").strip()), None)
    if analysed:
        secondary = " ".join(x for x in (analysed.get("description", ""), analysed.get("notes", "")) if x)
        direction = ("Approved style-card image analysis is the primary visual authority: "
                     + analysed["image_analysis"])
        if secondary:
            direction += " Compatible written style notes only: " + secondary
    elif production.get("visual_style_custom", "").strip():
        direction = production["visual_style_custom"]
    else:
        written_card = next((card for card in styles if card.get("description", "").strip()
                             or card.get("notes", "").strip()), None)
        if written_card:
            direction = " ".join(x for x in (written_card.get("description", ""), written_card.get("notes", "")) if x)
        elif production.get("style_bible", "").strip():
            direction = production["style_bible"]
        else:
            direction = production.get("visual_style_preset", "cinematic_realism").replace("_", " ")
    return ("Single visual-style authority for this clip and every camera beat: " + direction
            + " Keep the same rendering medium, character design, palette, lighting logic and texture. "
              "A style reference transfers treatment only, never its depicted people, props or location.")


def raw_estimate_seconds(text):
    han = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    punctuation = len(re.findall(r"[，。！？；,.!?;:]", text))
    speech = han / 4.2 + words / 2.45 + min(2.5, punctuation * .16)
    action = 1.7 + max(1, punctuation) * .75 + len(text) / 100
    return max(action, speech + 1.8)


def estimate_seconds(text):
    return max(PLANNED_MIN_SECONDS, min(MAX_SECONDS, math.ceil(raw_estimate_seconds(text))))


def fit_planned_durations(segments, target_seconds, language="en", source_story=None):
    """Preserve the plan while making its generated duration match the episode target."""
    result = copy.deepcopy(segments)
    count = len(result)
    target = int(round(target_seconds))
    timed_groups = timed_clip_groups(source_story) if source_story is not None else []
    if timed_groups:
        minimum = len(timed_groups)
        maximum = min(
            MAX_SEGMENTS,
            sum(max(1, group["duration"] // PLANNED_MIN_SECONDS) for group in timed_groups),
        )
        if not minimum <= count <= maximum:
            raise ValueError(
                f"The planner returned {count} clips for {minimum} source-timed groups; "
                f"safe subdivision permits {minimum}-{maximum} clips.")
    if not count or target < count * PLANNED_MIN_SECONDS or target > count * MAX_SECONDS:
        raise ValueError(
            f"The planned clip count cannot cover the {target}-second episode target with 5-15 second clips.")
    original = [int(item["duration"]) for item in result]
    preferred = ([group["duration"] for group in timed_groups]
                 if timed_groups and len(timed_groups) == count else original)
    durations = [min(MAX_SECONDS, max(PLANNED_MIN_SECONDS, value)) for value in preferred]
    difference = target - sum(durations)
    while difference:
        if difference > 0:
            candidates = [index for index, value in enumerate(durations) if value < MAX_SECONDS]
            if not candidates:
                raise ValueError("The episode target exceeds the available clip duration.")
            index = min(candidates, key=lambda value: (durations[value], value))
            durations[index] += 1
            difference -= 1
        else:
            candidates = [index for index, value in enumerate(durations) if value > PLANNED_MIN_SECONDS]
            if not candidates:
                raise ValueError("The episode target is shorter than the available clip duration.")
            index = max(candidates, key=lambda value: (durations[value], -value))
            durations[index] -= 1
            difference += 1
    labels = {
        "zh-CN": "按本集总时长校准为 {duration} 秒（全集合计 {target} 秒）",
        "zh-TW": "按本集總時長校準為 {duration} 秒（全集合計 {target} 秒）",
        "ja": "話全体の目標尺に合わせて{duration}秒に調整（合計{target}秒）",
        "en": "Aligned to {duration}s for the {target}s episode target",
    }
    for item, before, duration in zip(result, original, durations):
        item["duration"] = duration
        if before != duration:
            note = labels.get(language, labels["en"]).format(duration=duration, target=target)
            item["duration_reason"] = (item.get("duration_reason", "").rstrip(".。；; ") + "; " + note).lstrip("; ")
    return result


def _split_near_middle(value):
    if len(value) < 2:
        return None
    middle = len(value) / 2
    candidates = [match.end() for match in re.finditer(r"[。！？!?；;，,：:\n]|\s+", value)
                  if len(value) * .2 <= match.end() <= len(value) * .8]
    cut = min(candidates, key=lambda position: abs(position - middle)) if candidates else len(value) // 2
    left, right = value[:cut].strip(), value[cut:].strip()
    return (left, right) if left and right else None


def fallback_segments(story, target_seconds=None, language="zh-CN"):
    """Safe offline split when the selected local model is unavailable."""
    timed_groups = timed_clip_groups(story)
    if timed_groups:
        language_name = PRODUCTION_LANGUAGES.get(language, PRODUCTION_LANGUAGES["zh-CN"])
        result = []
        for index, group in enumerate(timed_groups):
            # Offline planning cannot translate safely, but it must never erase
            # authored dialogue. Preserve source words and let the warning/UI
            # make the unavailable translation explicit.
            dialogue = [{"speaker": line["speaker"], "text": line["text"],
                          "language": language_name, "voiceover": False}
                        for line in script_dialogue(group["text"])]
            result.append({
                "title": f"片段 {index + 1}", "story": group["text"], "setting": "",
                "action": group["text"], "ending": "动作完成并保持可衔接的画面状态",
                "duration": max(PLANNED_MIN_SECONDS, min(MAX_SECONDS, group["duration"])),
                "duration_reason": f"依据原剧本 {group['start']}–{group['end']} 秒自然节拍",
                "dialogue": dialogue, "image_prompt": group["text"],
                "cast_timeline": {key: [] for key in CAST_TIMELINE_KEYS},
                "transition_mode": "hard_cut",
                "card_selection": {kind: [] for kind in SELECTABLE_CARD_KINDS}})
        return (fit_planned_durations(result, target_seconds, language, story)
                if target_seconds is not None else result)
    coarse = [x.strip() for x in re.split(r"(?<=[。！？!?；;])\s*|\n+", story) if x.strip()]
    units = []
    for item in coarse:
        if raw_estimate_seconds(item) <= MAX_SECONDS:
            units.append(item)
            continue
        clauses = [x.strip() for x in re.split(r"(?<=[，,：:])\s*", item) if x.strip()]
        for clause in clauses:
            if raw_estimate_seconds(clause) <= MAX_SECONDS:
                units.append(clause)
            else:
                # Last-resort protection for an unpunctuated paragraph. The AI
                # planner normally finds semantic boundaries; offline mode must
                # still avoid claiming an impossible 15-second clip.
                units.extend(clause[i:i + 54] for i in range(0, len(clause), 54))
    if not units:
        raise ValueError("Write a story, script or shot outline before planning clips.")
    groups, current = [], []
    for unit in units:
        candidate = "".join(current + [unit])
        if current and raw_estimate_seconds(candidate) > MAX_SECONDS:
            groups.append("".join(current))
            current = [unit]
        else:
            current.append(unit)
    if current:
        groups.append("".join(current))
    if target_seconds is not None:
        desired = recommended_clip_count(target_seconds)
        while len(groups) < desired:
            index = max(range(len(groups)), key=lambda value: len(groups[value]))
            parts = _split_near_middle(groups[index])
            if parts is None:
                break
            groups[index:index + 1] = list(parts)
        while len(groups) > desired:
            index = min(range(len(groups) - 1), key=lambda value: len(groups[value]) + len(groups[value + 1]))
            groups[index:index + 2] = [groups[index].rstrip() + "\n" + groups[index + 1].lstrip()]
    result = []
    for index, value in enumerate(groups[:MAX_SEGMENTS]):
        duration = estimate_seconds(value)
        result.append({
            "title": f"片段 {index + 1}", "story": value, "setting": "",
            "action": value, "ending": "动作完成并保持可衔接的画面状态",
            "duration": duration,
            "duration_reason": f"本地估算：对白、动作与停顿合计约 {duration} 秒",
            "dialogue": [], "image_prompt": value,
            "cast_timeline": {key: [] for key in CAST_TIMELINE_KEYS},
            "transition_mode": "hard_cut",
            "card_selection": {kind: [] for kind in SELECTABLE_CARD_KINDS}})
    return fit_planned_durations(result, target_seconds, language) if target_seconds is not None else result


def segment_hash(segment):
    keys = ("title", "story", "setting", "action", "ending", "duration", "dialogue", "card_selection",
            "cast_timeline", "transition_mode", "prompt_direction", "keyframe_asset_ids", "workflow_profile_id")
    context = {key: segment.get(key) for key in keys}
    if segment.get("continue_previous") is False:
        context["continue_previous"] = False
    return _hash(context)


def _safe_ids(values, label, maximum):
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError(f"{label} must contain at most {maximum} local files.")
    result = []
    for value in values:
        value = safe_id(value)
        if value not in result:
            result.append(value)
    return result


def _seconds(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Recorded operation time must be a non-negative finite number.")
    return round(float(value), 3)


def normalise_segment(value, index, previous=None):
    if not isinstance(value, dict):
        raise ValueError("Every production segment must be an object.")
    duration = value.get("duration")
    if type(duration) is not int or not MIN_SECONDS <= duration <= MAX_SECONDS:
        raise ValueError("Every clip duration must be a whole number from 4 through 15 seconds.")
    prior = previous or {}
    legacy_asset = value.get("image_asset_id", prior.get("image_asset_id"))
    raw_keyframes = value.get("keyframe_asset_ids", prior.get("keyframe_asset_ids", [])) or []
    if legacy_asset and legacy_asset not in raw_keyframes:
        raw_keyframes = [*raw_keyframes, legacy_asset]
    keyframe_asset_ids = _safe_ids(raw_keyframes, "Clip keyframes", MAX_SEGMENT_KEYFRAMES)
    raw_runs = value.get("image_run_ids", prior.get("image_run_ids", [])) or []
    legacy_run = value.get("image_run_id", prior.get("image_run_id"))
    if legacy_run and legacy_run not in raw_runs:
        raw_runs = [*raw_runs, legacy_run]
    image_run_ids = _safe_ids(raw_runs, "Clip image requests", 24)
    transition_mode = _text(value.get("transition_mode", prior.get("transition_mode")),
                            "clip transition mode", 32)
    if not transition_mode:
        transition_mode = "continuous" if value.get(
            "continue_previous", prior.get("continue_previous", True)) else "hard_cut"
    if transition_mode not in TRANSITION_MODES:
        transition_mode = "hard_cut"
    explicit_timeline = isinstance(value.get("cast_timeline"), dict)
    timeline_version = value.get("cast_timeline_version", prior.get("cast_timeline_version"))
    if type(timeline_version) is not int or timeline_version < 0:
        timeline_version = CAST_TIMELINE_VERSION if explicit_timeline else 0
    result = {
        "id": prior.get("id", str(uuid.uuid4())), "index": index + 1,
        "title": _text(value.get("title"), "segment title", 120, f"片段 {index + 1}") or f"片段 {index + 1}",
        "story": _text(value.get("story"), "segment story", 3000),
        "setting": _text(value.get("setting"), "segment setting", 1000),
        "action": _text(value.get("action"), "segment action", 3000),
        "ending": _text(value.get("ending"), "segment ending", 1200),
        "duration": duration,
        "transition_mode": transition_mode,
        "continue_previous": value.get(
            "continue_previous", prior.get("continue_previous", transition_mode == "continuous")),
        "duration_reason": _text(value.get("duration_reason"), "duration reason", 500),
        "dialogue": [],
        "image_prompt": _text(value.get("image_prompt"), "image prompt", 3000),
        "prompt_direction": _text(value.get("prompt_direction", prior.get("prompt_direction")),
                                  "video prompt revision", 6000),
        "video_prompt": _text(value.get("video_prompt", prior.get("video_prompt")),
                              "compiled video prompt", 250000),
        "video_prompt_source": _text(value.get("video_prompt_source", prior.get("video_prompt_source")),
                                     "video prompt source", 30),
        "prompt_seconds": _seconds(value.get("prompt_seconds", prior.get("prompt_seconds"))),
        "prompt_updated_at": value.get("prompt_updated_at", prior.get("prompt_updated_at")),
        "prepare_seconds": _seconds(value.get("prepare_seconds", prior.get("prepare_seconds"))),
        "workflow_profile_id": _text(value.get("workflow_profile_id", prior.get("workflow_profile_id")),
                                     "video workflow profile", 40, "builtin") or "builtin",
        "card_selection": normalise_card_selection(value.get("card_selection")),
        "cast_timeline": normalise_cast_timeline(
            value.get("cast_timeline", prior.get("cast_timeline")),
            (value.get("card_selection") or {}).get("characters", [])),
        "cast_timeline_version": timeline_version,
        "continuity_warnings": list(value.get("continuity_warnings", prior.get("continuity_warnings", [])) or []),
        "card_selection_source": _text(value.get("card_selection_source"), "card selection source", 30,
                                       prior.get("card_selection_source", "heuristic")) or "heuristic",
        "project_id": prior.get("project_id"), "status": prior.get("status", "unprepared"),
        "source_hash": prior.get("source_hash", ""), "image_run_id": image_run_ids[-1] if image_run_ids else None,
        "image_run_ids": image_run_ids,
        "context_hash": prior.get("context_hash", ""),
        "reference_strategy_version": prior.get("reference_strategy_version", 0),
        "image_asset_id": keyframe_asset_ids[-1] if keyframe_asset_ids else None,
        "keyframe_asset_ids": keyframe_asset_ids,
        "stale_reasons": list(prior.get("stale_reasons", [])),
        "selected_video_run_id": prior.get("selected_video_run_id"),
        "last_video_run_id": prior.get("last_video_run_id"),
    }
    if result["selected_video_run_id"]:
        safe_id(result["selected_video_run_id"])
    if type(result["continue_previous"]) is not bool:
        raise ValueError("Clip continuation choice must be true or false.")
    if (not isinstance(result["continuity_warnings"], list) or
            not all(isinstance(item, str) for item in result["continuity_warnings"])):
        raise ValueError("Clip continuity warnings must be text entries.")
    result["continuity_warnings"] = [item[:360] for item in result["continuity_warnings"][:16]]
    if result["last_video_run_id"]:
        safe_id(result["last_video_run_id"])
    if result["workflow_profile_id"] != "builtin":
        safe_id(result["workflow_profile_id"])
    if result["prompt_updated_at"] is not None and (
            type(result["prompt_updated_at"]) not in (int, float) or
            not math.isfinite(result["prompt_updated_at"]) or result["prompt_updated_at"] < 0):
        raise ValueError("Video prompt update time is invalid.")
    if result["video_prompt_source"] not in ("", "local_ai", "compiled"):
        result["video_prompt_source"] = "compiled"
    if result["card_selection_source"] not in ("local_ai", "heuristic"):
        result["card_selection_source"] = "heuristic"
    if type(result["reference_strategy_version"]) is not int or result["reference_strategy_version"] < 0:
        result["reference_strategy_version"] = 0
    dialogue = value.get("dialogue", [])
    if not isinstance(dialogue, list) or len(dialogue) > 16:
        raise ValueError("A clip can contain at most sixteen dialogue events.")
    for line in dialogue:
        if not isinstance(line, dict) or type(line.get("voiceover", False)) is not bool:
            raise ValueError("Dialogue entries must contain speaker, text, language and voiceover.")
        result["dialogue"].append({
            "speaker": _text(line.get("speaker"), "dialogue speaker", 100),
            "text": _text(line.get("text"), "dialogue text", 1000),
            "language": _text(line.get("language"), "dialogue language", 60, "Chinese") or "Chinese",
            "voiceover": line.get("voiceover", False)})
    if result["project_id"] and result["source_hash"] != segment_hash(result):
        result["status"] = "stale"
        result["stale_reasons"] = ["分镜内容或时长在 H3 工程创建后发生了变化"]
    return result


class ProductionManager:
    def __init__(self, data_dir, load_project, save_project, load_asset, store_asset=None):
        self.data_dir = Path(data_dir).resolve()
        self.directory = (self.data_dir / "productions").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.collection_directory = (self.data_dir / "card_collections").resolve()
        self.collection_directory.mkdir(parents=True, exist_ok=True)
        self.collection_archive_directory = (self.data_dir / "card_collection_archive").resolve()
        self.collection_archive_directory.mkdir(parents=True, exist_ok=True)
        self.load_project, self.save_project, self.load_asset = load_project, save_project, load_asset
        self.store_asset = store_asset

    def _path(self, ident):
        return self.directory / (safe_id(ident) + ".json")

    def get(self, ident):
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("Production not found.")
        return self.validate(json.loads(path.read_text(encoding="utf-8")))

    def list(self):
        values = []
        for path in self.directory.glob("*.json"):
            try:
                item = self.validate(json.loads(path.read_text(encoding="utf-8")))
                values.append({"id": item["id"], "title": item["title"],
                    "updated_at": item["updated_at"], "segment_count": len(item["segments"]),
                    "ready_count": sum(s["status"] == "ready" for s in item["segments"]),
                    "episode_count": item["episode_count"], "current_episode": item["current_episode"]})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(values, key=lambda x: x["updated_at"], reverse=True)

    def validate(self, value):
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("This is not a version 1 production.")
        safe_id(value.get("id"))
        safe_id(value.get("source_project_id"))
        result = copy.deepcopy(value)
        source_mode = "ref2va"
        if not result.get("source_mode"):
            try:
                source_mode = self.load_project(result["source_project_id"]).get("mode", source_mode)
            except Exception:
                pass
        result["source_mode"] = _text(result.get("source_mode"), "source H3 mode", 12, source_mode) or source_mode
        if result["source_mode"] not in ("ref2va", "i2va", "fl2va", "l2va", "t2va"):
            raise ValueError("Production source mode is not supported.")
        result["title"] = _text(result.get("title"), "production title", 160, "Untitled production") or "Untitled production"
        result["language"] = _text(result.get("language"), "production language", 12, "zh-CN") or "zh-CN"
        if result["language"] not in PRODUCTION_LANGUAGES:
            raise ValueError("Production language must be zh-CN, zh-TW, en or ja.")
        result["prompt_version"] = _text(result.get("prompt_version"), "prompt version", 32, "classic") or "classic"
        if result["prompt_version"] not in PROMPT_VERSIONS:
            raise ValueError("Choose the original or director-continuity prompt version.")
        for key, limit in (("brief", 30000), ("style_bible", 12000),
                           ("character_bible", 20000), ("continuity_notes", 12000),
                           ("series_voice_style", 20000)):
            result[key] = _text(result.get(key), key, limit)
        result["visual_style_preset"] = _text(result.get("visual_style_preset"), "visual style preset", 80, "cinematic_realism") or "cinematic_realism"
        result["visual_style_custom"] = _text(result.get("visual_style_custom"), "custom visual style", 6000)
        result["narrative_style"] = _text(result.get("narrative_style"), "narrative style", 80, "cinematic") or "cinematic"
        result["narrative_style_custom"] = _text(result.get("narrative_style_custom"), "custom narrative style", 6000)
        result["narrative_notes"] = _text(result.get("narrative_notes"), "narrative notes", 6000)
        collection_id = result.get("card_collection_id")
        if collection_id:
            safe_id(collection_id)
        result["card_collection_id"] = collection_id or None
        result["card_collection_name"] = _text(result.get("card_collection_name"), "card collection name", 120, "Main cast") or "Main cast"
        result["cards"] = normalise_cards(result.get("cards"))
        # Voice cards own identity and performance; the active production owns
        # the language actually spoken. Applying a collection therefore adapts
        # its target language without altering the saved source collection.
        for voice in result["cards"]["voices"]:
            voice["language"] = result["language"]
        result["overview_asset_ids"] = normalise_overview_assets(result.get("overview_asset_ids"))
        result["auto_merge"] = result.get("auto_merge", True)
        if type(result["auto_merge"]) is not bool:
            raise ValueError("Automatic film assembly must be true or false.")
        result["auto_continue_previous"] = result.get("auto_continue_previous", False)
        if type(result["auto_continue_previous"]) is not bool:
            raise ValueError("Automatic continuation from the preceding clip must be true or false.")
        result["auto_keyframes_enabled"] = result.get("auto_keyframes_enabled", False)
        if type(result["auto_keyframes_enabled"]) is not bool:
            raise ValueError("Automatic supplemental keyframes must be true or false.")
        result["auto_keyframe_model"] = _text(result.get("auto_keyframe_model"), "image model", 160,
                                               "z_image_turbo_bf16.safetensors") or "z_image_turbo_bf16.safetensors"
        result["video_aspect_ratio"] = _text(result.get("video_aspect_ratio"), "video aspect ratio", 8, "16:9") or "16:9"
        if result["video_aspect_ratio"] not in ("16:9", "9:16", "1:1", "4:3", "3:4"):
            raise ValueError("Choose a supported production aspect ratio.")
        result["video_resolution"] = _text(result.get("video_resolution"), "video resolution", 8, "0.7") or "0.7"
        if result["video_resolution"] not in ("0.3", "0.5", "0.7", "1.0"):
            raise ValueError("Choose a supported production video resolution.")
        result["video_quality"] = _text(result.get("video_quality"), "video quality", 16, "fast") or "fast"
        if result["video_quality"] not in ("fast", "detailed", "lora8"):
            raise ValueError("Choose a supported production quality recipe.")
        if result["video_quality"] == "lora8" and result["source_mode"] != "ref2va":
            raise ValueError("The 8-step LoRA recipe requires Reference-to-Video mode.")
        video_steps = result.get("video_steps", "auto")
        if video_steps not in ("auto", 4, 8, 16):
            raise ValueError("Production video steps must be auto, 4, 8 or 16.")
        result["video_steps"] = video_steps
        result["task_state"] = _text(result.get("task_state"), "task state", 16, "active") or "active"
        if result["task_state"] not in ("active", "paused"):
            raise ValueError("Production task state must be active or paused.")
        raw_timings = result.get("timings", {})
        if not isinstance(raw_timings, dict):
            raise ValueError("Production timings must be an object.")
        result["timings"] = {key: _seconds(raw_timings.get(key)) for key in TIMING_KEYS}
        generated = result.get("generated_overviews", {})
        if not isinstance(generated, dict) or len(generated) > 64:
            generated = {}
        clean_generated = {}
        for digest, asset_id in generated.items():
            if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) and isinstance(asset_id, str):
                safe_id(asset_id)
                clean_generated[digest] = asset_id
        result["generated_overviews"] = clean_generated
        character_ids = {card["id"] for card in result["cards"]["characters"]}
        for card in result["cards"]["wardrobe"] + result["cards"]["props"]:
            if card.get("owner_card_id") and card["owner_card_id"] not in character_ids:
                card["owner_card_id"] = None
        for card in result["cards"]["voices"]:
            if card.get("character_card_id") and card["character_card_id"] not in character_ids:
                card["character_card_id"] = None
        voice_ids = {card["id"] for card in result["cards"]["voices"]}
        for card in result["cards"]["characters"]:
            if card.get("voice_card_id") and card["voice_card_id"] not in voice_ids:
                card["voice_card_id"] = None
        episode_count = result.get("episode_count", 1)
        if type(episode_count) is not int or not 1 <= episode_count <= MAX_EPISODES:
            raise ValueError(f"Episode count must be 1-{MAX_EPISODES}.")
        result["episode_count"] = episode_count
        episode_minutes = result.get("episode_minutes", 8)
        if type(episode_minutes) not in (int, float) or not 0.5 <= episode_minutes <= 180:
            raise ValueError("Episode target length must be 0.5-180 minutes.")
        result["episode_minutes"] = float(episode_minutes)
        raw_episodes = result.get("episodes", [])
        if not isinstance(raw_episodes, list) or len(raw_episodes) > MAX_EPISODES:
            raise ValueError(f"A production can contain at most {MAX_EPISODES} episode plans.")
        seen_character_ids = set()
        episodes = []
        for index, episode in enumerate(raw_episodes):
            cleaned = normalise_episode(episode, index, character_ids, seen_character_ids)
            seen_character_ids.update(cleaned["character_card_ids"])
            episodes.append(cleaned)
        result["episodes"] = episodes
        current_episode = result.get("current_episode", 1)
        if type(current_episode) is not int:
            current_episode = 1
        result["current_episode"] = max(1, min(current_episode, max(1, len(episodes) or episode_count)))
        result["episode_planner"] = _text(result.get("episode_planner"), "episode planner", 40)
        result["episode_planner_warning"] = _text(result.get("episode_planner_warning"), "episode planner warning", 1000)
        result["card_planner"] = _text(result.get("card_planner"), "card planner", 40)
        result["card_planner_warning"] = _text(result.get("card_planner_warning"), "card planner warning", 1000)
        result["card_plan_source_hash"] = _text(result.get("card_plan_source_hash"), "card plan source hash", 128)
        segments = result.get("segments", [])
        if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
            raise ValueError(f"A production can contain at most {MAX_SEGMENTS} clips.")
        result["segments"] = [normalise_segment(item, i, item) for i, item in enumerate(segments)]
        current_context_hash = production_context_hash(result)
        upstream_changed = False
        for segment in result["segments"]:
            reasons = []
            if segment["project_id"] and segment["source_hash"] != segment_hash(segment):
                reasons.append("分镜内容或时长在 H3 工程创建后发生了变化")
            if segment["project_id"] and segment.get("context_hash") != current_context_hash:
                reasons.append("全片约束或资产卡已变化")
            if (segment["project_id"] and
                    segment.get("reference_strategy_version", 0) != REFERENCE_STRATEGY_VERSION):
                reasons.append("参考图分配规则已升级，请重新生成本段提示词后再生成视频")
            if (segment["project_id"] and
                    segment.get("cast_timeline_version", 0) != CAST_TIMELINE_VERSION):
                reasons.append("角色入场、退场与结尾在场规则已升级，请先重新规划本集分镜")
            own_change = bool(reasons)
            if own_change:
                segment["status"] = "stale"
                segment["stale_reasons"] = reasons
            if upstream_changed and segment["project_id"] and not own_change:
                segment["status"] = "stale"
                segment["stale_reasons"] = ["前序分镜已变化，人物、道具或运动连续性需要重新确认"]
            upstream_changed = upstream_changed or own_change
        return result

    def save(self, value):
        result = self.validate(value)
        result["updated_at"] = time.time()
        atomic_json(self._path(result["id"]), result)
        return result

    def delete(self, ident):
        ident = safe_id(ident)
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("Production not found.")
        production = self.get(ident)
        has_library = (
            any(production["cards"][kind] for kind in CARD_KINDS)
            or any(production["overview_asset_ids"].values())
            or bool(production.get("series_voice_style"))
        )
        preserved = self.save_card_collection(
            ident, production.get("card_collection_name") or production["title"] + " cards"
        ) if has_library else None
        archive = (self.data_dir / "production_archive").resolve()
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / f"{ident}-{int(time.time())}.json"
        path.replace(target)
        return {"deleted": True, "id": ident,
                "linked_projects_preserved": True, "video_results_preserved": True,
                "card_library_preserved": True,
                "card_collection_id": preserved["id"] if preserved else None,
                "card_collection_name": preserved["name"] if preserved else None}

    def create(self, body):
        source = check_project(body.get("source_project"))
        self.save_project(source)
        now = time.time()
        result = {
            "schema_version": 1, "id": str(uuid.uuid4()),
            "title": _text(body.get("title"), "production title", 160, source["title"]) or source["title"],
            "language": _text(body.get("language"), "production language", 12, "zh-CN") or "zh-CN",
            "brief": _text(body.get("brief"), "brief", 30000, source["story"]["text"]),
            "style_bible": _text(body.get("style_bible"), "style bible", 12000,
                "；".join(x for x in source.get("style", {}).values() if x)),
            "character_bible": _text(body.get("character_bible"), "character bible", 20000,
                "\n".join(f"{s['name']}: {s.get('description', '')}" for s in source["subjects"])),
            "continuity_notes": _text(body.get("continuity_notes"), "continuity notes", 12000),
            "series_voice_style": _text(body.get("series_voice_style"), "series voice style", 20000),
            "prompt_version": body.get("prompt_version", "classic"),
            "visual_style_preset": _text(body.get("visual_style_preset"), "visual style preset", 80, "cinematic_realism") or "cinematic_realism",
            "visual_style_custom": _text(body.get("visual_style_custom"), "custom visual style", 6000),
            "narrative_style": _text(body.get("narrative_style"), "narrative style", 80, "cinematic") or "cinematic",
            "narrative_style_custom": _text(body.get("narrative_style_custom"), "custom narrative style", 6000),
            "narrative_notes": _text(body.get("narrative_notes"), "narrative notes", 6000),
            "episode_count": body.get("episode_count", 1), "episode_minutes": body.get("episode_minutes", 8),
            "current_episode": 1, "episodes": [], "episode_planner": "", "episode_planner_warning": "",
            "card_planner": "", "card_planner_warning": "", "card_plan_source_hash": "",
            "card_collection_id": None,
            "card_collection_name": _text(body.get("card_collection_name"), "card collection name", 120, source["title"] + " cast") or "Main cast",
            "source_project_id": source["id"], "source_mode": source["mode"], "created_at": now, "updated_at": now,
            "cards": cards_from_project(source),
            "overview_asset_ids": empty_overview_assets(),
            "auto_merge": True,
            "auto_continue_previous": False,
            "auto_keyframes_enabled": False,
            "auto_keyframe_model": "z_image_turbo_bf16.safetensors",
            "video_aspect_ratio": body.get("video_aspect_ratio", source.get("aspect_ratio", "16:9")),
            "video_resolution": body.get("video_resolution", source.get("comfy_render", {}).get("resolution", "0.7")),
            "video_quality": body.get("video_quality", source.get("comfy_render", {}).get("quality", "fast")),
            "video_steps": body.get("video_steps", source.get("comfy_render", {}).get("steps", "auto")),
            "task_state": "active",
            "timings": {key: None for key in TIMING_KEYS},
            "generated_overviews": {},
            "planner": None, "planner_warning": None, "segments": []}
        return self.save(result)

    def update(self, ident, body):
        current = self.get(ident)
        allowed = {"title", "language", "prompt_version", "brief", "style_bible", "character_bible", "continuity_notes", "cards", "segments", "auto_merge", "auto_continue_previous", "task_state", "auto_keyframes_enabled", "auto_keyframe_model",
                   "video_aspect_ratio", "video_resolution", "video_quality", "video_steps",
                   "visual_style_preset", "visual_style_custom", "narrative_style", "narrative_style_custom", "narrative_notes", "episode_count", "episode_minutes",
                   "current_episode", "episodes", "card_collection_id", "card_collection_name", "overview_asset_ids", "series_voice_style"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Production update contains unsupported fields.")
        for key in allowed:
            if key in body:
                current[key] = copy.deepcopy(body[key])
        return self.save(current)

    def apply_episode_plan(self, ident, planned, planner, warning=None):
        current = self.get(ident)
        canonical = {card["name"].strip().casefold(): card["id"] for card in current["cards"]["characters"]}
        seen, episodes = set(), []
        for index, item in enumerate(planned[:current["episode_count"]]):
            item = copy.deepcopy(item)
            item["character_card_ids"] = list(dict.fromkeys(
                canonical[name.strip().casefold()] for name in item.get("character_names", [])
                if isinstance(name, str) and name.strip().casefold() in canonical))
            if index < len(current["episodes"]):
                item["id"] = current["episodes"][index]["id"]
            episode = normalise_episode(item, index, set(canonical.values()), seen)
            seen.update(episode["character_card_ids"])
            episodes.append(episode)
        if len(episodes) != current["episode_count"]:
            raise ValueError("Episode planner did not return the requested number of episodes.")
        current["episodes"] = episodes
        current["episode_planner"] = planner
        current["episode_planner_warning"] = warning or ""
        current["current_episode"] = min(current["current_episode"], len(episodes))
        current["segments"] = []
        current["planner"] = None
        current["planner_warning"] = None
        return self.save(current)

    def list_card_collections(self):
        values = []
        for path in self.collection_directory.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                safe_id(item["id"])
                cards = normalise_cards(item.get("cards"))
                overviews = normalise_overview_assets(item.get("overview_asset_ids"))
                values.append({"id": item["id"], "name": _text(item.get("name"), "collection name", 120),
                               "updated_at": float(item.get("updated_at", 0)),
                               "card_count": sum(len(rows) for rows in cards.values()),
                               "counts": {kind: len(cards[kind]) for kind in CARD_KINDS},
                               "overview_count": sum(bool(asset_id) for asset_id in overviews.values()),
                               "has_series_voice_style": bool(_text(item.get("series_voice_style"), "series voice style", 20000))})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(values, key=lambda item: item["updated_at"], reverse=True)

    def get_card_collection(self, ident):
        path = self.collection_directory / (safe_id(ident) + ".json")
        if not path.is_file():
            raise ValueError("Card collection not found.")
        item = json.loads(path.read_text(encoding="utf-8"))
        return {"id": safe_id(item.get("id")), "name": _text(item.get("name"), "collection name", 120),
                "cards": normalise_cards(item.get("cards")),
                "series_voice_style": _text(item.get("series_voice_style"), "series voice style", 20000),
                "overview_asset_ids": normalise_overview_assets(item.get("overview_asset_ids")),
                "updated_at": float(item.get("updated_at", 0))}

    def save_card_collection(self, production_id, name):
        production = self.get(production_id)
        ident = production.get("card_collection_id") or str(uuid.uuid4())
        existing_path = self.collection_directory / (ident + ".json")
        existing = self.get_card_collection(ident) if existing_path.is_file() else None
        cards = copy.deepcopy(existing["cards"]) if existing else empty_card_library()
        id_maps = {kind: {card["id"]: card["id"] for card in cards[kind]}
                   for kind in CARD_KINDS}
        # A production is a snapshot of its shared set. Saving a later part of
        # a series must not erase cards that only appeared in earlier parts.
        for kind in ("characters", "wardrobe", "props", "environments", "styles", "voices"):
            by_id = {card["id"]: card for card in cards[kind]}
            by_name = {card["name"].strip().casefold(): card for card in cards[kind]}
            for source in production["cards"][kind]:
                target = by_id.get(source["id"]) or by_name.get(source["name"].strip().casefold())
                if target is None:
                    target = copy.deepcopy(source)
                    cards[kind].append(target)
                    by_name[target["name"].strip().casefold()] = target
                else:
                    # Keep the stable shared ID and every reference file. New
                    # non-empty edits win, but an empty local placeholder must
                    # not wipe the reusable card's identity or description.
                    for field in ("description", "image_analysis", "image_generation_prompt", "notes", "voice_id", "pace",
                                  "subject_id", "owner_card_id", "character_card_id", "voice_card_id"):
                        if source.get(field):
                            target[field] = source[field]
                    target["asset_ids"] = list(dict.fromkeys(target["asset_ids"] + source["asset_ids"]))
                    target["locked"] = source["locked"]
                id_maps[kind][source["id"]] = target["id"]
        for kind in ("wardrobe", "props"):
            for card in cards[kind]:
                card["owner_card_id"] = id_maps["characters"].get(card.get("owner_card_id"), card.get("owner_card_id"))
        for card in cards["voices"]:
            card["character_card_id"] = id_maps["characters"].get(card.get("character_card_id"), card.get("character_card_id"))
        for card in cards["characters"]:
            card["voice_card_id"] = id_maps["voices"].get(card.get("voice_card_id"), card.get("voice_card_id"))
        cards = normalise_cards(cards)
        overviews = {kind: production["overview_asset_ids"].get(kind) or
                     (existing["overview_asset_ids"].get(kind) if existing else None)
                     for kind in OVERVIEW_CARD_KINDS}
        now = time.time()
        value = {"schema_version": 1, "id": ident,
                 "name": _text(name, "collection name", 120, production["card_collection_name"]) or "Main cast",
                 "cards": cards, "series_voice_style": production["series_voice_style"] or
                 (existing["series_voice_style"] if existing else ""),
                 "overview_asset_ids": overviews, "updated_at": now}
        atomic_json(existing_path, value)
        production["card_collection_id"] = ident
        production["card_collection_name"] = value["name"]
        self.save(production)
        return {**value, "card_count": sum(len(rows) for rows in value["cards"].values()),
                "overview_count": sum(bool(asset_id) for asset_id in value["overview_asset_ids"].values())}

    def rename_card_collection(self, ident, name):
        collection = self.get_card_collection(ident)
        collection["name"] = _text(name, "collection name", 120)
        if not collection["name"]:
            raise ValueError("Give the card set a name.")
        collection["schema_version"] = 1
        collection["updated_at"] = time.time()
        atomic_json(self.collection_directory / (collection["id"] + ".json"), collection)
        # A project stores its own cards. Only refresh the display name for projects
        # that still point to this reusable set.
        for path in self.directory.glob("*.json"):
            try:
                production = self.validate(json.loads(path.read_text(encoding="utf-8")))
                if production.get("card_collection_id") == collection["id"]:
                    production["card_collection_name"] = collection["name"]
                    self.save(production)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return self.get_card_collection(collection["id"])

    def duplicate_card_collection(self, ident, name=None):
        source = self.get_card_collection(ident)
        duplicate = copy.deepcopy(source)
        duplicate["schema_version"] = 1
        duplicate["id"] = str(uuid.uuid4())
        duplicate["name"] = _text(name, "collection name", 120, source["name"] + " copy") or source["name"] + " copy"
        duplicate["updated_at"] = time.time()
        atomic_json(self.collection_directory / (duplicate["id"] + ".json"), duplicate)
        return self.get_card_collection(duplicate["id"])

    def delete_card_collection(self, ident):
        ident = safe_id(ident)
        path = self.collection_directory / (ident + ".json")
        if not path.is_file():
            raise ValueError("Card collection not found.")
        collection = self.get_card_collection(ident)
        target = self.collection_archive_directory / f"{ident}-{int(time.time())}.json"
        path.replace(target)
        # Projects already contain independent card copies and asset references.
        # Detach the missing template without removing any project card or media.
        for project_path in self.directory.glob("*.json"):
            try:
                production = self.validate(json.loads(project_path.read_text(encoding="utf-8")))
                if production.get("card_collection_id") == ident:
                    production["card_collection_id"] = None
                    production["card_collection_name"] = collection["name"]
                    self.save(production)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return {"deleted": True, "id": ident, "name": collection["name"], "archived": True,
                "project_cards_preserved": True, "assets_preserved": True}

    def apply_card_collection(self, production_id, collection_id):
        production, collection = self.get(production_id), self.get_card_collection(collection_id)
        merged = empty_card_library()
        id_maps = {kind: {} for kind in CARD_KINDS}

        def merge_kind(kind):
            by_name = {}
            for card in production["cards"][kind]:
                copied = copy.deepcopy(card)
                merged[kind].append(copied)
                by_name[copied["name"].strip().casefold()] = copied
            for raw in collection["cards"][kind]:
                card = copy.deepcopy(raw)
                if kind in ("wardrobe", "props") and card.get("owner_card_id"):
                    card["owner_card_id"] = id_maps["characters"].get(card["owner_card_id"], card["owner_card_id"])
                if kind == "voices" and card.get("character_card_id"):
                    card["character_card_id"] = id_maps["characters"].get(card["character_card_id"], card["character_card_id"])
                existing = by_name.get(card["name"].strip().casefold())
                if existing is None:
                    merged[kind].append(card)
                    by_name[card["name"].strip().casefold()] = card
                    existing = card
                else:
                    # Same-name placeholders in a new project should still
                    # receive the set's images and missing descriptive data.
                    existing["asset_ids"] = list(dict.fromkeys(existing["asset_ids"] + card["asset_ids"]))
                    for field in ("description", "image_analysis", "image_generation_prompt", "notes", "voice_id", "pace"):
                        if not existing.get(field) and card.get(field):
                            existing[field] = card[field]
                id_maps[kind][raw["id"]] = existing["id"]

        # Character links must be known before owner/voice cards are merged.
        merge_kind("characters")
        for kind in ("wardrobe", "props", "environments", "styles", "voices"):
            merge_kind(kind)
        # Complete the reverse character -> voice link after voice IDs are mapped.
        merged_characters = {card["id"]: card for card in merged["characters"]}
        for source in collection["cards"]["characters"]:
            target = merged_characters.get(id_maps["characters"].get(source["id"], ""))
            mapped_voice = id_maps["voices"].get(source.get("voice_card_id"))
            if target is not None and not target.get("voice_card_id") and mapped_voice:
                target["voice_card_id"] = mapped_voice
        production["cards"] = merged
        production["overview_asset_ids"] = {
            kind: production["overview_asset_ids"].get(kind) or collection["overview_asset_ids"].get(kind)
            for kind in OVERVIEW_CARD_KINDS}
        production["series_voice_style"] = production.get("series_voice_style") or collection.get("series_voice_style", "")
        production["card_collection_id"] = collection["id"]
        production["card_collection_name"] = collection["name"]
        return self.save(production)

    def apply_card_plan(self, production_id, planned, planner="local_ai", warning=None):
        """Merge an AI text-card draft without overwriting user-authored material."""
        production = self.get(production_id)
        required = {"series_voice_style", "characters", "wardrobe", "props", "environments", "voices", "styles"}
        if not isinstance(planned, dict) or set(planned) != required:
            raise ValueError("The AI card draft is incomplete.")
        for kind in CARD_KINDS:
            if not isinstance(planned[kind], list):
                raise ValueError("The AI card draft contains an invalid card list.")

        def merge(kind, raw, links=None):
            name = _text(raw.get("name") if isinstance(raw, dict) else None, "AI card name", 120)
            if not name:
                return None
            existing = next((card for card in production["cards"][kind]
                             if card["name"].strip().casefold() == name.casefold()), None)
            incoming = {
                "name": name,
                "description": _text(raw.get("description"), "AI card description", 3000),
                "notes": _text(raw.get("notes"), "AI card notes", 1200),
                "locked": True,
                **(links or {}),
            }
            if kind == "characters":
                # AI drafts may ignore the reusable-card instruction and copy
                # a one-off scene into the library. Filter only the incoming AI
                # text; established user-authored cards are never rewritten.
                for key in ("description", "notes"):
                    incoming[key] = render_character_identity({"description": incoming[key], "notes": ""})
            if existing is None:
                existing = normalise_card(incoming, kind, len(production["cards"][kind]))
                production["cards"][kind].append(existing)
                return existing
            for key in ("description", "notes"):
                if not existing.get(key) and incoming.get(key):
                    existing[key] = incoming[key]
            for key, value in (links or {}).items():
                if value and not existing.get(key):
                    existing[key] = value
            return existing

        for raw in planned["characters"]:
            merge("characters", raw)
        character_by_name = {card["name"].strip().casefold(): card
                             for card in production["cards"]["characters"]}
        for kind in ("wardrobe", "props"):
            for raw in planned[kind]:
                owner_name = _text(raw.get("owner_character") if isinstance(raw, dict) else None,
                                   "AI card owner", 120)
                owner = character_by_name.get(owner_name.casefold()) if owner_name else None
                merge(kind, raw, {"owner_card_id": owner["id"] if owner else None})
        for raw in planned["environments"]:
            merge("environments", raw)
        for raw in planned["styles"]:
            merge("styles", raw)
        for raw in planned["voices"]:
            if not isinstance(raw, dict):
                continue
            character_name = _text(raw.get("character_name"), "AI voice character", 120)
            character = character_by_name.get(character_name.casefold()) if character_name else None
            if character is None:
                continue
            voice = merge("voices", raw, {"character_card_id": character["id"]})
            if voice is not None:
                if not voice.get("voice_id"):
                    voice["voice_id"] = _text(raw.get("voice_id"), "AI voice ID", 100)
                if not voice.get("pace"):
                    voice["pace"] = _text(raw.get("pace"), "AI voice pace", 160)
                if not voice.get("language"):
                    voice["language"] = production["language"]
                if not character.get("voice_card_id"):
                    character["voice_card_id"] = voice["id"]
        if not production.get("series_voice_style"):
            production["series_voice_style"] = _text(
                planned.get("series_voice_style"), "AI series voice style", 6000)
        production["card_planner"] = planner
        production["card_planner_warning"] = warning or ""
        production["card_plan_source_hash"] = card_plan_source_hash(production)
        return self.save(production)

    def apply_plan(self, ident, planned, planner, warning=None):
        current, prepared = self.get(ident), []
        old = current["segments"]
        locked_groups = locked_timed_dialogue(current)
        alias_sets = character_aliases(current)
        character_by_alias = {
            alias: card for card in current["cards"]["characters"]
            for alias in alias_sets.get(card["id"], {card["name"].strip().casefold()})
        }
        planned = copy.deepcopy(planned)
        if any(group.get("dialogue_parse_failed") for group in locked_groups):
            raise ValueError(
                "The source screenplay appears to contain quoted dialogue, but its speaker format could not be parsed. "
                "No storyboard was saved; review the speaker cue instead of silently losing dialogue.")

        def speaker_key(value):
            value = re.split(r"[|｜]", str(value or "").strip().casefold(), maxsplit=1)[0].strip()
            card = character_by_alias.get(value)
            return card["name"].strip().casefold() if card else value

        def translated_dialogue(item, locked):
            source = locked.get("source_dialogue", locked.get("dialogue", []))
            actual = item.get("dialogue", [])
            if len(actual) != len(source):
                raise ValueError(
                    "The plan omitted, invented or combined authored dialogue while translating it.")
            for source_line, actual_line in zip(source, actual):
                if speaker_key(source_line.get("speaker")) != speaker_key(actual_line.get("speaker")):
                    raise ValueError(
                        "The plan reassigned or reordered authored dialogue while translating it.")
                if not str(actual_line.get("text", "")).strip():
                    raise ValueError("The plan returned an empty translated dialogue line.")
                # Lines already written in the requested output language remain
                # byte-for-byte source authority even inside a mixed-language cue.
                if _dialogue_already_matches_language(source_line.get("text", ""), current["language"]):
                    actual_line["text"] = source_line["text"]
                actual_line["speaker"] = source_line["speaker"]
                actual_line["language"] = PRODUCTION_LANGUAGES[current["language"]]
                actual_line["voiceover"] = bool(source_line.get("voiceover", False))
            return actual

        if locked_groups and len(locked_groups) == len(planned):
            for item, locked in zip(planned, locked_groups):
                # The local model may improve staging, but the authored
                # dialogue track is byte-for-byte authority. An empty authored
                # track is also a lock: never let the model move later dialogue
                # into an earlier silent beat or invent a line there.
                if locked.get("requires_translation"):
                    item["dialogue"] = translated_dialogue(item, locked)
                else:
                    item["dialogue"] = copy.deepcopy(locked["dialogue"])
        elif locked_groups:
            # A dense authored time block may legitimately become two generation
            # clips. In that case the line can move to either child clip, but the
            # exact source words and speaker must still appear once and in order.
            expected = [line for group in locked_groups
                        for line in group.get("source_dialogue", group.get("dialogue", []))]
            actual = [line for item in planned for line in item.get("dialogue", [])]
            if len(actual) != len(expected):
                raise ValueError(
                    "A subdivided plan omitted, changed, reassigned or reordered locked source dialogue.")
            for source_line, actual_line in zip(expected, actual):
                same_speaker = speaker_key(source_line.get("speaker")) == speaker_key(actual_line.get("speaker"))
                exact_required = _dialogue_already_matches_language(
                    source_line.get("text", ""), current["language"])
                same_text = str(source_line.get("text", "")).strip() == str(actual_line.get("text", "")).strip()
                if not same_speaker or (exact_required and not same_text) or not str(actual_line.get("text", "")).strip():
                    raise ValueError(
                        "A subdivided plan omitted, changed, reassigned or reordered locked source dialogue.")
                if exact_required:
                    actual_line["text"] = source_line["text"]
                actual_line["speaker"] = source_line["speaker"]
                actual_line["language"] = PRODUCTION_LANGUAGES[current["language"]]
                actual_line["voiceover"] = bool(source_line.get("voiceover", False))
        departed_characters, continuity_corrections = set(), []
        for index, item in enumerate(planned):
            item = copy.deepcopy(item)
            segment_corrections = []
            # Canonicalise and complete card choices for both the AI plan and
            # the safe timecode fallback. If the model fails, exact character
            # names and speaking voices must still follow the authored beat.
            if isinstance(item.get("card_selection"), dict):
                canonical = {kind: {card["name"].strip().casefold(): card["name"]
                                   for card in current["cards"][kind]}
                             for kind in SELECTABLE_CARD_KINDS}
                canonical["characters"].update({alias: card["name"] for alias, card in character_by_alias.items()})
                item["card_selection"] = {kind: list(dict.fromkeys(
                    canonical[kind][name.strip().casefold()]
                    for name in item["card_selection"].get(kind, [])
                    if isinstance(name, str) and name.strip().casefold() in canonical[kind]))
                    for kind in SELECTABLE_CARD_KINDS}
                visible_text = "\n".join(str(item.get(key, "")) for key in
                                         ("story", "setting", "action", "ending", "image_prompt")).casefold()
                character_by_name = {card["name"].strip().casefold(): card
                                     for card in current["cards"]["characters"]}
                selected_characters = [name for name in item["card_selection"]["characters"]
                                       if not character_is_embedded_form(
                                           character_by_name.get(name.casefold(), {}), visible_text)]
                # A local-AI cast list is authoritative. The deterministic
                # fallback may infer a cast only when it has no explicit choice;
                # otherwise scanning context silently recruits every character
                # who is merely mentioned in a long screenplay beat.
                if planner != "local_ai" and not selected_characters:
                    for card in current["cards"]["characters"]:
                        named = any(_name_occurs(alias, visible_text)
                                    for alias in alias_sets.get(card["id"], {card["name"].strip().casefold()}))
                        if named and not character_is_embedded_form(card, visible_text):
                            selected_characters.append(card["name"])
                voices_by_character = {}
                character_names_by_id = {card["id"]: card["name"] for card in current["cards"]["characters"]}
                for voice in current["cards"]["voices"]:
                    owner = character_names_by_id.get(voice.get("character_card_id"))
                    if owner:
                        voices_by_character[owner.casefold()] = voice["name"]
                visible_speaker_names = []
                for line in item.get("dialogue", []):
                    speaker_key = str(line.get("speaker", "")).strip().casefold()
                    # Screenplays commonly append acting direction after a
                    # vertical bar. It is not part of the character's name.
                    speaker_name = re.split(r"[|｜]", speaker_key, maxsplit=1)[0].strip()
                    character = (character_by_alias.get(speaker_name) or character_by_name.get(speaker_name) or
                                 character_by_alias.get(speaker_key) or character_by_name.get(speaker_key))
                    if character:
                        # Store the reusable canonical name so voice cards and
                        # prompt subjects remain bound in every UI language.
                        line["speaker"] = character["name"]
                        speaker_key = character["name"].strip().casefold()
                    if (character and not line.get("voiceover") and
                            not character_is_embedded_form(character, visible_text) and
                            character["name"] not in selected_characters):
                        selected_characters.append(character["name"])
                    if character and not line.get("voiceover"):
                        visible_speaker_names.append(character["name"])
                    voice_name = voices_by_character.get(speaker_key)
                    if voice_name and voice_name not in item["card_selection"]["voices"]:
                        item["card_selection"]["voices"].append(voice_name)
                raw_timeline = normalise_cast_timeline(item.get("cast_timeline"), selected_characters)

                def canonical_character_names(values):
                    names = []
                    for value in values:
                        name = canonical["characters"].get(str(value).strip().casefold())
                        card = character_by_name.get(name.casefold()) if name else None
                        if (name and name not in names and
                                not character_is_embedded_form(card or {}, visible_text)):
                            names.append(name)
                    return names

                timeline = {key: canonical_character_names(raw_timeline[key])
                            for key in CAST_TIMELINE_KEYS}
                temporal_visible = list(dict.fromkeys(
                    timeline["visible_start"] + timeline["enters"] + timeline["exits"] +
                    timeline["visible_end"] + visible_speaker_names))
                for speaker_name in visible_speaker_names:
                    if speaker_name not in temporal_visible:
                        temporal_visible.append(speaker_name)
                    if (speaker_name not in timeline["visible_start"] and
                            speaker_name not in timeline["enters"]):
                        timeline["visible_start"].append(speaker_name)
                    if (speaker_name not in timeline["visible_end"] and
                            speaker_name not in timeline["exits"]):
                        timeline["visible_end"].append(speaker_name)

                entering = {name.casefold() for name in timeline["enters"]}
                departed_characters.difference_update(entering)
                illegal = [name for name in temporal_visible
                           if name.casefold() in departed_characters and name.casefold() not in entering]
                for name in illegal:
                    for key in ("visible_start", "visible_end", "exits"):
                        timeline[key] = [value for value in timeline[key] if value != name]
                    if name not in timeline["mentioned_only"]:
                        timeline["mentioned_only"].append(name)
                    message = (f"Clip {index + 1}: removed {name} from the visible cast because the character "
                               "previously exited and no new entrance was declared.")
                    segment_corrections.append(message)

                reentered = {name.casefold() for name in timeline["enters"]}
                for name in list(timeline["exits"]):
                    if name in timeline["visible_end"] and name.casefold() not in reentered:
                        timeline["visible_end"].remove(name)
                        segment_corrections.append(
                            f"Clip {index + 1}: removed exiting character {name} from final-frame visibility.")

                temporal_visible = list(dict.fromkeys(
                    timeline["visible_start"] + timeline["enters"] + timeline["exits"] +
                    timeline["visible_end"] + visible_speaker_names))[:16]
                visible_folded = {name.casefold() for name in temporal_visible}
                timeline["offscreen"] = [name for name in timeline["offscreen"]
                                         if name.casefold() not in visible_folded]
                excluded = visible_folded | {name.casefold() for name in timeline["offscreen"]}
                timeline["mentioned_only"] = [name for name in timeline["mentioned_only"]
                                               if name.casefold() not in excluded]
                item["cast_timeline"] = timeline
                item["cast_timeline_version"] = CAST_TIMELINE_VERSION
                item["card_selection"]["characters"] = temporal_visible
                item["card_selection"]["voices"] = item["card_selection"]["voices"][:3]
                mode = item.get("transition_mode")
                if mode not in TRANSITION_MODES:
                    mode = "hard_cut"
                if index == 0:
                    mode = "hard_cut"
                item["transition_mode"] = mode
                item["continue_previous"] = index > 0 and mode == "continuous"

                ending_visible = {name.casefold() for name in timeline["visible_end"]}
                # Only a narrative departure persists across clips. Local
                # models often put performers in ``exits`` merely because they
                # walk through a doorway, climb out of the current framing, or
                # disappear during a dissolve. Treating those shot exits as a
                # permanent story exit strips their cards from every later
                # clip and leaves H3 to invent replacement people.
                departed_characters.update(
                    name.casefold() for name in timeline["exits"]
                    if (name.casefold() not in ending_visible and
                        persistent_story_departure(name, item, mode)))
                departed_characters.difference_update(ending_visible)
                item["continuity_warnings"] = segment_corrections
                continuity_corrections.extend(segment_corrections)
            # Replanning an episode is a revision of its existing timeline, not
            # a destructive replacement, when the ordered clip count remains
            # the same. Preserve clip/project/video identities so completed
            # takes stay visible; validation marks changed prompts stale before
            # another render. If the count changes, only exact matches retain a
            # prior identity because ordinal clips may have shifted meaning.
            prior = (old[index] if index < len(old) and (
                len(old) == len(planned) or segment_hash(old[index]) == segment_hash(item)) else None)
            segment = normalise_segment(item, index, prior)
            segment["card_selection_source"] = (
                "local_ai" if planner == "local_ai" and isinstance(item.get("card_selection"), dict)
                else "heuristic")
            prepared.append(segment)
        if continuity_corrections:
            guard_note = ("Continuity guard corrected temporal cast conflicts: " +
                          " ".join(continuity_corrections[:6]))
            warning = (warning + " " + guard_note).strip() if warning else guard_note
        current["segments"], current["planner"], current["planner_warning"] = prepared, planner, warning
        return self.save(current)

    def materialise(self, ident, segment_id):
        started = time.monotonic()
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment:
            raise ValueError("Production clip not found.")
        if segment.get("cast_timeline_version", 0) != CAST_TIMELINE_VERSION:
            raise ValueError(
                "This storyboard predates temporal cast tracking. Replan this episode before rebuilding its video prompts.")
        source = copy.deepcopy(self.load_project(production["source_project_id"]))
        existing_id = segment.get("project_id")
        try:
            base = copy.deepcopy(self.load_project(existing_id)) if existing_id else copy.deepcopy(source)
        except Exception:
            existing_id = None
            base = copy.deepcopy(source)
        base["id"] = existing_id or str(uuid.uuid4())
        base["title"] = f"{production['title']} · {segment['index']:02d} {segment['title']}"
        base["production_language"] = production["language"]
        base["prompt_version"] = production["prompt_version"]
        base["duration"] = segment["duration"]
        base["aspect_ratio"] = production["video_aspect_ratio"]
        base["story"] = {"text": segment["story"] or segment["action"], "locked": True}
        # Sound belongs to this exact clip. Never inherit an unrelated room tone
        # or score from the Studio master/previous materialised clip; the local
        # planner may author fresh values and the safe compiler otherwise leaves
        # them unspecified.
        base["soundscape"] = ""
        base["music"] = ""
        base["authoring_mode"] = "full"
        simple = base.get("simple") if isinstance(base.get("simple"), dict) else {}
        base["simple"] = {**simple, "directed": True, "approach": "cinematic"}
        render = base.setdefault("comfy_render", {})
        # A production clip is a new authored beat. Never inherit an unrelated
        # MMH3 input that happened to be selected in the Studio master when the
        # production was created. A future explicit clip-continuation action
        # can add its own verified source after a successful preceding take.
        for key in ("continuation_source", "continuation_overlap_frames", "duration_basis"):
            render.pop(key, None)
        base["simple"].pop("continuation", None)
        profile_id = segment.get("workflow_profile_id", "builtin")
        use_ref8 = profile_id == REF8_WORKFLOW_ID or (
            profile_id == "builtin" and production["video_quality"] == "lora8")
        if render.get("workflow_profile_id") == REF8_WORKFLOW_ID and not use_ref8:
            # Rebuilding after switching back must not leave the optional
            # adapter or sigma shifts inside the ordinary workflow.
            source_render = source.get("comfy_render", {})
            for key in ("loras", "shift_video", "shift_audio"):
                if key in source_render:
                    render[key] = copy.deepcopy(source_render[key])
                else:
                    render.pop(key, None)
        # An explicitly selected custom workflow keeps its own established
        # behavior instead of receiving the bundled attachment's LoRA recipe.
        effective_quality = ("fast" if production["video_quality"] == "lora8" and not use_ref8
                             else production["video_quality"])
        render.update({
            "workflow_profile_id": REF8_WORKFLOW_ID if use_ref8 else profile_id,
            "aspect_ratio": production["video_aspect_ratio"],
            "resolution": production["video_resolution"],
            "quality": effective_quality,
            "steps": production["video_steps"],
        })
        if use_ref8:
            render.update(ref8_recipe_settings())
        card_selection = self._apply_cards(base, production, segment)
        relevant_cards = self._relevant_cards(production, segment)
        def clip_text(value):
            return canonicalise_character_mentions(value, production, relevant_cards["characters"])

        # Render exact selected-card names into the detached clip project. The
        # source episode and card library retain their authored language.
        base["story"]["text"] = clip_text(segment["story"] or segment["action"])
        # A project-level style/card selection must reach the actual H3 prompt,
        # not only the local planner. The production style bible copied from
        # the source project remains the fallback when no card was authored.
        base["style"] = {"genre": continuity_visual_lock(production, relevant_cards),
                         "vibe": "", "lighting": "", "color": "", "notes": ""}
        names = {s["name"].strip().casefold(): s for s in base["subjects"]}
        lines, visible, offscreen, selected_voices, selected_voice_ids = [], [], [], [], set()
        collective_subjects = []
        selected_names = {card["name"].strip().casefold() for card in relevant_cards["characters"]}
        for line in segment["dialogue"]:
            key = line["speaker"].strip().casefold()
            subject = names.get(key)
            is_collective = (collective_speaker(line["speaker"]) and not line["voiceover"]
                             and key not in selected_names)
            if subject is None:
                subject = {"id": uid(), "name": line["speaker"] or "Narrator",
                           "description": "Production dialogue speaker; add identity references if visible.",
                           "asset_ids": []}
                base["subjects"].append(subject)
                names[key] = subject
            if is_collective:
                subject["description"] = "Collective dialogue cue for the selected visible cast; not an additional character."
                collective_subjects.append(subject)
            voice = None if is_collective else self._voice_for_subject(production, subject["id"], subject["name"])
            if voice and voice["id"] not in selected_voice_ids:
                selected_voice_ids.add(voice["id"])
                selected_voices.append(voice)
            voice_delivery = "; ".join(x for x in [
                "follow locked voice card " + voice["name"] if voice else "",
                voice.get("pace", "") if voice else "",
                "voice identity " + voice.get("voice_id", "") if voice and voice.get("voice_id") else "",
                "voice target language " + PRODUCTION_LANGUAGES[production["language"]] if voice else "",
            ] if x)
            lines.append({"id": uid(), "speaker_id": subject["id"], "text": line["text"],
                "language": PRODUCTION_LANGUAGES[production["language"]], "delivery": voice_delivery or "natural and unhurried",
                "locked": True, "voiceover": line["voiceover"]})
            if not is_collective:
                (offscreen if line["voiceover"] else visible).append(subject["id"])
        # Selected character cards describe the on-camera cast even when a
        # different character owns the only spoken line. Voiceover speakers
        # remain off-screen and must never acquire physical staging merely
        # because their identity card was selected for continuity.
        offscreen_ids = set(offscreen)
        visible = [subject_id for subject_id in card_selection["subject_ids"] + visible
                   if subject_id not in offscreen_ids]
        if not visible and len(base["subjects"]) == 1:
            visible = [s["id"] for s in base["subjects"]
                       if s["id"] not in offscreen_ids and not s.get("collective_member_ids")]
        visible = list(dict.fromkeys(visible))
        if collective_subjects and not visible:
            raise ValueError("Collective dialogue needs at least one selected visible character")
        for collective in collective_subjects:
            collective["collective_member_ids"] = list(visible)
        cast_lock = temporal_cast_lock(segment)
        final_cast = ", ".join(segment["cast_timeline"]["visible_end"]) or "none"
        scene = shot(segment["duration"])
        scene.update({"action": "\n\n".join(x for x in [
                clip_text(segment["action"] or segment["story"]), cast_lock] if x),
            "setting": clip_text(segment["setting"]),
            "final_state": "\n\n".join(x for x in [
                clip_text(segment["ending"]),
                f"FINAL-FRAME CAST LOCK: show exactly {final_cast}; every other named identity is absent."
            ] if x), "dialogue": lines,
            "visible_subject_ids": visible,
            "offscreen_subject_ids": [x for x in dict.fromkeys(offscreen) if x not in visible],
            "director_locks": ["setting", "final_state", "visible_subject_ids", "offscreen_subject_ids"]})
        base["shots"] = [scene]
        voice_context = voice_prompt_context(production, selected_voices)
        custom_style = production.get('visual_style_custom', '')
        style_bible = production.get('style_bible', '')
        distinct_style_bible = style_bible if style_bible.strip().casefold() != custom_style.strip().casefold() else ""
        internal_timing = clip_text(authored_internal_timing(production, segment))
        planning_context = "\n\n".join(x for x in [
            "LONG-FORM PRODUCTION CONTEXT (facts and continuity constraints, not new events):",
            f"Episode: {production.get('current_episode', 1)} of {production.get('episode_count', 1)}.",
            f"Visual style preset: {production.get('visual_style_preset', 'cinematic_realism')}.",
            f"Custom visual style: {custom_style}" if custom_style else "",
            f"Visual style bible: {distinct_style_bible}" if distinct_style_bible else "",
            "STYLE PRIORITY: analysed style-card images override custom style text; custom style text overrides the named preset. Transfer visual treatment only, never depicted subjects or scene content.",
            f"Long-form narrative style: {production.get('narrative_style', 'cinematic')}.",
            f"Custom narrative style: {production.get('narrative_style_custom', '')}" if production.get("narrative_style_custom") else "",
            f"Narrative notes: {production.get('narrative_notes', '')}" if production.get("narrative_notes") else "",
            ("Current clip character bible:\n" + scoped_character_bible(production, relevant_cards["characters"])
             if relevant_cards["characters"] else ""),
            "CHARACTER AUTHORITY SPLIT: User-uploaded character images are the primary authority for directly visible appearance—face, hair, apparent age, body proportions, silhouette and distinctive visible marks. The Character Bible and matching named card remain authoritative for name, identity, relationships, non-visible facts and image-to-character binding. Text may add explicit continuity requirements that the image cannot show. Never merge, rename or swap characters, and never let automated image analysis override either the pixels or the written identity binding.",
            f"Continuity notes: {production['continuity_notes']}" if production["continuity_notes"] else "",
            f"Project output language: {PRODUCTION_LANGUAGES[production['language']]}. Keep all dialogue and generated text in this language.",
             card_context(production, relevant_cards, include_characters=False),
             cast_lock,
             "Preserve exact dialogue. Stage only this clip. End in the declared visible state for continuity."] if x)
        # The local planner needs the complete production bible, but H3 should
        # receive only renderable scene direction. Keeping these channels
        # separate prevents the final prompt from repeating the character bible,
        # cards and overview policy for every subject. Voice cards and authored
        # beat timing remain delivery instructions because the renderer needs
        # those exact constraints.
        # A production may be created from an earlier materialised clip. Its
        # custom_instructions are clip-specific generated delivery directions
        # (timing, voice roster, revision), not a reusable project bible.
        # Inheriting them leaked an unrelated old scene into every new clip.
        source_instructions = "" if source.get("production_link") else source.get("custom_instructions", "")
        narrative_version = production["prompt_version"] in ("continuity_director", "storyboard_narrative")
        if narrative_version:
            characters_by_id = {card["id"]: card for card in production["cards"]["characters"]}
            local_subjects_by_name = {subject["name"].strip().casefold(): subject["id"]
                                      for subject in base["subjects"]}
            base["narrative_voice"] = {
                "series_style": production.get("series_voice_style", ""),
                "cards": [{
                    "subject_id": local_subjects_by_name[
                        characters_by_id[card["character_card_id"]]["name"].strip().casefold()],
                    "name": card["name"], "description": card.get("description", ""),
                    "notes": card.get("notes", ""), "voice_id": card.get("voice_id", ""),
                    "has_audio": bool(card.get("asset_ids")),
                } for card in selected_voices
                    if card.get("character_card_id") in characters_by_id
                    and characters_by_id[card["character_card_id"]]["name"].strip().casefold()
                    in local_subjects_by_name],
            }
        else:
            base.pop("narrative_voice", None)
        delivery_context = "\n\n".join(x for x in [
            source_instructions,
            internal_timing,
            "" if narrative_version else voice_context,
            f"CLIP PROMPT REVISION REQUEST: {segment['prompt_direction']}" if segment.get("prompt_direction") else "",
        ] if x)
        # Re-materialising replaces both production-managed channels instead of
        # appending another copy from the previous clip project.
        base["production_planning_context"] = planning_context
        base["custom_instructions"] = "\n\n".join(
            x for x in [delivery_context] if x)
        base["h3_verbatim_blocks"] = ([voice_context] if not narrative_version and voice_context and not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", voice_context) else [])
        # A saved delivery-language record is cryptographically bound to the
        # previous compiled prompt. Rebuilding cards, cast or dialogue makes it
        # stale by definition; remove it together with the segment prompt so a
        # clean compile can be shown and the next AI pass starts from this exact
        # materialised project.
        base.pop("h3_prompt_translation", None)
        base["production_link"] = {"production_id": production["id"], "production_title": production["title"],
            "segment_id": segment["id"], "segment_title": segment["title"],
            "segment_index": segment["index"], "source_hash": segment_hash(segment),
            "reference_strategy": card_selection}
        for asset_id in segment.get("keyframe_asset_ids", []):
            existing = next((a for a in base["assets"] if a["id"] == asset_id), None)
            if existing is not None:
                existing.update(enabled=True, role="context", production_segment_id=segment["id"])
                continue
            asset = copy.deepcopy(self.load_asset(asset_id))
            asset.update(enabled=True, role="context",
                         semantic_role=asset.get("semantic_role", "background"),
                         production_segment_id=segment["id"])
            base["assets"].append(asset)
        check_project(base)
        self.save_project(base)
        # Materialisation reconstructs the clip project from the current cards,
        # references and storyboard. Any previously saved delivery prompt was
        # compiled from an older project snapshot and must not remain displayed
        # as a synced preview if the following AI/compile step is interrupted.
        segment["video_prompt"] = ""
        segment["video_prompt_source"] = ""
        segment["prompt_seconds"] = None
        segment["prompt_updated_at"] = None
        segment["project_id"], segment["status"] = base["id"], "ready"
        segment["source_hash"], segment["context_hash"] = segment_hash(segment), production_context_hash(production)
        segment["reference_strategy_version"] = REFERENCE_STRATEGY_VERSION
        segment["prepare_seconds"] = round(time.monotonic() - started, 3)
        segment["stale_reasons"] = []
        production = self.save(production)
        return {"production": production, "project": base}

    def _voice_for_subject(self, production, subject_id, subject_name):
        characters = production["cards"]["characters"]
        character = next((card for card in characters if card.get("subject_id") == subject_id), None)
        if character is None:
            character = next((card for card in characters if card["name"].strip().casefold() == subject_name.strip().casefold()), None)
        if character is None:
            return None
        return next((card for card in production["cards"]["voices"]
            if card.get("character_card_id") == character["id"] or character.get("voice_card_id") == card["id"]), None)

    def _asset_in_project(self, project, asset_id, kind, owner_subject_id=None):
        asset = next((item for item in project["assets"] if item["id"] == asset_id), None)
        if asset is None:
            asset = copy.deepcopy(self.load_asset(asset_id))
            project["assets"].append(asset)
        media_type = asset.get("media_type")
        asset["enabled"] = True
        asset["role"] = "reference_audio" if kind == "voices" else "reference_image"
        asset["semantic_role"] = CARD_SEMANTIC_ROLES[kind]
        asset.setdefault("description", "")
        asset.setdefault("observation", "")
        asset.setdefault("approved_observation", "")
        asset.setdefault("locked_order", False)
        if owner_subject_id:
            if kind == "props":
                asset["simple_owner_id"] = owner_subject_id
            else:
                asset.pop("simple_owner_id", None)
            subject = next((item for item in project["subjects"] if item["id"] == owner_subject_id), None)
            if subject is not None and asset_id not in subject["asset_ids"]:
                subject["asset_ids"].append(asset_id)
        if kind == "voices" and media_type != "audio":
            raise ValueError("Voice cards accept clean audio files only.")
        if kind != "voices" and media_type != "image":
            raise ValueError("Visual cards accept image files only.")
        return asset

    def _ensure_overview(self, production, kind, cards):
        source_ids = [card["asset_ids"][0] for card in cards if card["asset_ids"]][:9]
        # Version the contact-sheet layout so older unnumbered cached sheets are
        # rebuilt once and cannot be mistaken for the new numbered region map.
        digest = _hash({"overview_version": 2, "kind": kind, "assets": source_ids})
        cached = production["generated_overviews"].get(digest)
        if cached:
            try:
                self.load_asset(cached)
                return cached
            except Exception:
                pass
        if not self.store_asset or len(source_ids) < 2:
            return source_ids[0] if source_ids else None
        from PIL import Image, ImageDraw, ImageOps
        columns = 3 if len(source_ids) > 4 else 2
        rows = math.ceil(len(source_ids) / columns)
        cell = 512
        canvas = Image.new("RGB", (columns * cell, rows * cell), (18, 24, 21))
        draw = ImageDraw.Draw(canvas)
        for index, asset_id in enumerate(source_ids):
            meta = self.load_asset(asset_id)
            path = self.data_dir / "assets" / asset_id / meta["filename"]
            with Image.open(path) as source:
                image = ImageOps.contain(source.convert("RGB"), (cell - 12, cell - 12))
                x = (index % columns) * cell + (cell - image.width) // 2
                y = (index // columns) * cell + (cell - image.height) // 2
                canvas.paste(image, (x, y))
            # A visible numeric badge gives the prompt's structured overview map
            # an unambiguous region marker without relying on CJK font support.
            left, top = (index % columns) * cell + 14, (index // columns) * cell + 14
            draw.rounded_rectangle((left, top, left + 58, top + 58), radius=12, fill=(9, 18, 13), outline=(182, 241, 211), width=3)
            draw.text((left + 22, top + 18), str(index + 1), fill=(236, 255, 246))
        buffer = io.BytesIO()
        canvas.save(buffer, "JPEG", quality=92, optimize=True)
        names = {"characters": "角色", "wardrobe": "服装", "props": "道具",
                 "environments": "环境", "styles": "风格"}
        asset = self.store_asset(buffer.getvalue(), f"自动{names.get(kind, kind)}总览拼图.jpg", "image/jpeg")
        production["generated_overviews"][digest] = asset["id"]
        return asset["id"]

    def _relevant_cards(self, production, segment):
        text = "\n".join(str(segment.get(key, "")) for key in
            ("title", "story", "setting", "action", "ending", "image_prompt")).casefold()
        speakers = {line.get("speaker", "").strip().casefold() for line in segment.get("dialogue", [])}
        alias_sets = character_aliases(production)
        speaker_cards = {card["id"] for card in production["cards"]["characters"]
                         if any(alias in speakers for alias in alias_sets.get(card["id"], set()))}
        if segment.get("card_selection_source") == "local_ai":
            selection = segment.get("card_selection", {})
            result = {}
            for kind in SELECTABLE_CARD_KINDS:
                chosen = {name.strip().casefold() for name in selection.get(kind, [])}
                result[kind] = [card for card in production["cards"][kind]
                                if card["name"].strip().casefold() in chosen and
                                (kind != "characters" or not character_is_embedded_form(card, text))]
            speaking_character_ids = speaker_cards
            result["voices"] = [card for card in production["cards"]["voices"]
                                if card.get("character_card_id") in speaking_character_ids]
            result["styles"] = production["cards"]["styles"][:1]
            return result
        selected_names = {
            kind: {str(name).strip().casefold()
                   for name in segment.get("card_selection", {}).get(kind, [])}
            for kind in SELECTABLE_CARD_KINDS}

        def mentioned(card, kind):
            name = card["name"].strip().casefold()
            if not name:
                return False
            if name in selected_names.get(kind, set()):
                return True
            if kind == "characters" and any(_name_occurs(alias, text)
                                             for alias in alias_sets.get(card["id"], {name})):
                return True
            # ASCII name substrings can accidentally recruit unrelated cast:
            # a card named "Yu" must not match "Yuki" in the screenplay.
            if re.search(r"[a-z0-9]", name):
                if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", text):
                    return True
            elif name in text:
                return True
            name_words = [word for word in re.findall(r"[a-z0-9]{3,}", name)
                          if word not in {"the", "and", "with", "from"}]
            words = [word for word in name_words
                     if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text)]
            cjk = "".join(re.findall(r"[\u3400-\u9fff]", name))
            bigrams = {cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))}
            matched_bigrams = {pair for pair in bigrams if pair in text}
            # Partial-name matching is useful for a long environment label, but
            # unsafe for props and wardrobe: seeing "Pokke" must not summon
            # every item named "Pokke's ...", and the word "道具" must not select
            # an entire overview card. Explicit local-AI selections still win.
            if kind != "environments":
                return False
            required_words = min(2, len(name_words)) if name_words else 0
            return bool((required_words and len(words) >= required_words) or len(matched_bigrams) >= 2)
        characters = production["cards"]["characters"]
        explicit_characters = selected_names.get("characters", set())
        if explicit_characters:
            # Once the storyboard has an explicit visible cast, do not expand
            # it by rescanning summaries and continuity notes. On-screen
            # speakers are the only safe required addition.
            active = [card for card in characters if
                      (card["name"].strip().casefold() in explicit_characters or
                       card["id"] in speaker_cards) and
                      not character_is_embedded_form(card, text)]
        else:
            active = [card for card in characters if
                      (card["id"] in speaker_cards or mentioned(card, "characters")) and
                      not character_is_embedded_form(card, text)]
        if not active:
            # An unnamed beat may plausibly contain the only known character;
            # a larger library is never a license to put everyone on screen.
            active = characters if len(characters) == 1 else []
        active_ids = {card["id"] for card in active}
        selected_wardrobe = [card for card in production["cards"]["wardrobe"]
                             if card.get("owner_card_id") in active_ids or mentioned(card, "wardrobe")]
        selected_props = [card for card in production["cards"]["props"] if mentioned(card, "props")]
        # A clip that explicitly shows a character-owned prop or wardrobe also
        # shows its owner unless the screenplay says otherwise. This closes the
        # common multilingual gap where prose says "the teacher holds the Ancient
        # Book" but the reusable card is named "Mr. Tsukiguma".
        visible_owner_ids = {card.get("owner_card_id") for card in (*selected_wardrobe, *selected_props)
                             if card.get("owner_card_id")}
        for card in characters:
            if (card["id"] in visible_owner_ids and card["id"] not in active_ids and
                    not character_is_embedded_form(card, text)):
                active.append(card)
                active_ids.add(card["id"])
        result = {"characters": active}
        result["wardrobe"] = [card for card in production["cards"]["wardrobe"]
                              if card.get("owner_card_id") in active_ids or mentioned(card, "wardrobe")]
        result["props"] = selected_props
        environments = [card for card in production["cards"]["environments"]
                        if mentioned(card, "environments")]
        if not environments and len(production["cards"]["environments"]) == 1:
            environments = production["cards"]["environments"]
        result["environments"] = environments
        result["styles"] = production["cards"]["styles"][:1]
        result["voices"] = [card for card in production["cards"]["voices"]
            if card.get("character_card_id") in active_ids and
               any(character["id"] == card.get("character_card_id") and character["name"].strip().casefold() in speakers
                   for character in active)]
        return result

    def _apply_cards(self, project, production, segment):
        characters, subjects = production["cards"]["characters"], project["subjects"]
        by_id = {subject["id"]: subject for subject in subjects}
        by_name = {subject["name"].strip().casefold(): subject for subject in subjects}
        subject_for_card, claimed_subject_ids = {}, set()
        for card in characters:
            identity_text = render_character_identity(card)
            name_key = card["name"].strip().casefold()
            # Exact names are stronger than legacy positional/ID links. A
            # subject already claimed by another character card can never be
            # reused, even when a damaged old card set points both cards at it.
            named = by_name.get(name_key)
            linked = by_id.get(card.get("subject_id"))
            subject = named if named and named["id"] not in claimed_subject_ids else None
            if subject is None and linked and linked["id"] not in claimed_subject_ids:
                subject = linked
            if subject is None:
                subject = {"id": uid(), "name": card["name"], "description": identity_text, "asset_ids": []}
                subjects.append(subject)
                by_id[subject["id"]] = subject
            else:
                subject["name"] = card["name"]
                subject["description"] = identity_text
            by_name[name_key] = subject
            claimed_subject_ids.add(subject["id"])
            # The render copy may create a clip-local subject ID. Do not write
            # that ID back into the reusable card library: another clip has a
            # different render copy and would otherwise invalidate every
            # previously prepared clip when its card is resolved.
            subject_for_card[card["id"]] = subject["id"]

        # Remove the library pool from the render copy, then add only references
        # relevant to this clip. This protects H3's 9-image/3-audio limits.
        card_asset_ids = {asset_id for kind in CARD_KINDS for card in production["cards"][kind]
                          for asset_id in card["asset_ids"]}
        card_asset_ids.update(production["generated_overviews"].values())
        card_asset_ids.update(asset_id for asset_id in production["overview_asset_ids"].values() if asset_id)
        # A text/reference-driven long-form clip is an isolated render unit. The
        # source Studio project can contain old people and references that were
        # never promoted into this production's card library. Carrying those
        # enabled assets forward silently creates ghost Subjects/Pictures in the
        # compiled prompt (for example a face reference left in the source
        # project). Treat the selected production cards as the identity/media
        # whitelist. This only mutates the deep-copied segment project; the
        # source Studio project and the reusable production card library remain
        # untouched. First/last-frame modes keep their source anchors because
        # those modes require them.
        if production.get("source_mode") in ("ref2va", "t2va"):
            project["assets"] = []
            for subject in subjects:
                subject["asset_ids"] = []
        else:
            project["assets"] = [asset for asset in project["assets"] if asset["id"] not in card_asset_ids]
        for subject in subjects:
            subject["asset_ids"] = [asset_id for asset_id in subject["asset_ids"] if asset_id not in card_asset_ids]

        relevant = self._relevant_cards(production, segment)
        active_subject_ids = [subject_for_card[card["id"]] for card in relevant["characters"]]
        if production.get("source_mode") in ("ref2va", "t2va"):
            active_subject_id_set = set(active_subject_ids)
            subjects[:] = [subject for subject in subjects if subject["id"] in active_subject_id_set]
        existing_images = sum(asset.get("enabled") and asset.get("role") == "reference_image"
                              for asset in project["assets"])
        image_budget = max(0, 9 - existing_images)
        selected_images, overview_kinds, overview_sources = [], [], {}
        plans = []
        for kind in ("characters", "environments", "props", "wardrobe"):
            candidates = list(relevant[kind])
            illustrated = [card for card in candidates if card["asset_ids"]]
            manual_overview = production["overview_asset_ids"].get(kind)
            if not candidates or (not illustrated and not manual_overview):
                continue
            missing_details = len(illustrated) < len(candidates)
            # Up to three visible characters use independent identity images.
            # Four or more are assembled into a clip-specific numbered sheet
            # containing exactly this clip's cast. This follows the production
            # card-library rule, saves H3 reference slots, and prevents a dense
            # multi-reference ensemble from re-instantiating one character
            # during a camera reveal. A full user library sheet may contain
            # absent cast, so the automatic clip-specific sheet is safer here.
            if kind == "characters" and len(illustrated) > 3:
                mode = "automatic"
            else:
                mode = "manual" if manual_overview and (missing_details or not illustrated) else "individual"
            plans.append({"kind": kind, "cards": candidates, "illustrated": illustrated,
                          "manual": manual_overview, "mode": mode})

        def plan_cost(plan):
            return 1 if plan["mode"] != "individual" else len(plan["illustrated"])

        # Preserve individual detail while it fits. When the total crosses H3's
        # nine-image ceiling, compress the categories that save the most slots.
        total_cost = sum(plan_cost(plan) for plan in plans)
        if total_cost > image_budget:
            compressible = sorted(
                (plan for plan in plans if plan["mode"] == "individual" and len(plan["illustrated"]) > 1),
                # Compress environments/props/wardrobe before character
                # identity. Within each tier, save the most slots first.
                key=lambda plan: (plan["kind"] == "characters", -(len(plan["illustrated"]) - 1),
                                  not bool(plan["manual"])))
            for plan in compressible:
                if total_cost <= image_budget:
                    break
                old_cost = plan_cost(plan)
                # A user-supplied all-cast sheet can contain people absent from
                # this clip. If cast compression is unavoidable, build a
                # clip-specific numbered sheet from exactly the selected
                # independent cards. Other categories may use their curated
                # overview safely.
                plan["mode"] = ("automatic" if plan["kind"] == "characters"
                                else ("manual" if plan["manual"] else "automatic"))
                total_cost -= old_cost - 1

        kind_labels = {"characters": "character", "wardrobe": "wardrobe",
                       "props": "prop", "environments": "environment"}
        for plan in plans:
            if image_budget <= 0:
                break
            kind, candidates, illustrated = plan["kind"], plan["cards"], plan["illustrated"]
            if plan["mode"] != "individual":
                asset_id = plan["manual"] if plan["mode"] == "manual" else self._ensure_overview(production, kind, illustrated)
                if asset_id:
                    asset = self._asset_in_project(project, asset_id, kind)
                    # One category overview can depict several separately named
                    # people/items. Keep the map as structured data instead of
                    # asking a vision model to infer their names from pixels.
                    covered_cards = candidates if plan["mode"] == "manual" else illustrated[:9]
                    bindings = []
                    for position, card in enumerate(covered_cards, 1):
                        owner_card_id = card["id"] if kind == "characters" else card.get("owner_card_id")
                        subject_id = subject_for_card.get(owner_card_id)
                        subject = next((item for item in subjects if item["id"] == subject_id), None)
                        canonical = (render_character_identity(card) if kind == "characters" else
                                     "\n".join(x for x in [card.get("description", ""), card.get("notes", "")] if x))
                        bindings.append({"card_id": card["id"], "name": card["name"],
                                         "subject_id": subject_id,
                                         "subject_name": subject.get("name", "") if subject else "",
                                         "canonical_description": canonical,
                                         "region": (f"numbered region {position}" if plan["mode"] == "automatic"
                                                    else f"the distinct figure matching {card['name']}'s written appearance "
                                                         "(ignore any conflicting printed label)")})
                        # Character and wardrobe overviews may intentionally
                        # supply several subjects from one Picture. Sharing this
                        # asset makes every Subject -> Picture binding explicit.
                        if subject is not None and kind in ("characters", "wardrobe") and asset_id not in subject["asset_ids"]:
                            subject["asset_ids"].append(asset_id)
                    names = ", ".join(card["name"] for card in covered_cards)
                    source = "user-supplied" if plan["mode"] == "manual" else "automatically assembled"
                    asset["reference_overview"] = True
                    asset["reference_card_kind"] = kind
                    asset["reference_card_names"] = [card["name"] for card in covered_cards]
                    asset["reference_card_bindings"] = bindings
                    asset["reference_facts_source"] = "character_bible_and_named_cards"
                    asset["description"] = (
                        f"{source.capitalize()} {kind_labels[kind]} library overview for this clip. "
                        f"Use it as a compact identity/continuity map for the selected cards: {names}. "
                        "Do not blend different entries together; match each named subject or item to its own region. "
                        "Each mapped image region is the primary authority for directly visible appearance. "
                        "The project Character Bible and matching named card remain authoritative for names, identity, relationships, non-visible facts and region binding. "
                        "Never rename, merge or swap identities, and never let automated image analysis override the visible pixels or written binding.")
                    selected_images.append(asset_id)
                    overview_kinds.append(kind)
                    overview_sources[kind] = plan["mode"]
                    image_budget -= 1
                continue
            for card in illustrated:
                if image_budget <= 0:
                    break
                asset_id = card["asset_ids"][0]
                owner = subject_for_card.get(card["id"] if kind == "characters" else card.get("owner_card_id"))
                asset = self._asset_in_project(project, asset_id, kind, owner)
                for key in ("reference_overview", "reference_card_kind", "reference_card_names",
                            "reference_card_bindings", "reference_facts_source"):
                    asset.pop(key, None)
                written = "\n".join(x for x in [card["description"], card["notes"]] if x)
                asset["description"] = "\n".join(x for x in [
                    (f"PRIMARY VISIBLE APPEARANCE AUTHORITY for the named {kind_labels[kind]} card {card['name']}. "
                     "Follow the uploaded image for directly visible shape, proportions, materials, colours, surface detail and distinctive marks. "
                     "Use the written card to identify the correct subject or item and to add explicit continuity facts not reliably visible; never replace clear image evidence with generic automated interpretation."),
                    written if kind != "characters" else "",
                ] if x)
                selected_images.append(asset_id)
                image_budget -= 1

        style_context = []
        for card in relevant["styles"]:
            if card["asset_ids"]:
                asset_id = card["asset_ids"][0]
                asset = self._asset_in_project(project, asset_id, "styles")
                asset["role"] = "context"
                asset["description"] = "\n".join(x for x in [
                    "This image is the highest-priority visual style authority. Transfer only its lighting, palette, contrast, rendering medium and texture; never copy depicted subjects, objects or location.",
                    card.get("image_analysis", ""), card["description"], card["notes"]] if x)
                asset["approved_observation"] = card.get("image_analysis", "") or asset.get("approved_observation", "")
                style_context.append(asset_id)

        selected_audio = []
        for card in relevant["voices"][:3]:
            owner = subject_for_card.get(card.get("character_card_id"))
            if not card["asset_ids"]:
                continue
            asset_id = card["asset_ids"][0]
            asset = self._asset_in_project(project, asset_id, "voices", owner)
            written = "\n".join(x for x in [card["description"], card["notes"]] if x)
            asset["description"] = "\n".join(x for x in [
                (f"PRIMARY VOICE IDENTITY AUTHORITY for {card['name']}. Follow this uploaded clean voice sample for timbre, pitch, accent, apparent age, cadence, pace and vocal texture. "
                 "Written direction supplements acting intent, emotional limits and exclusions. Never copy the sample's words; speak only the exact scripted dialogue in the project's target language."),
                written,
            ] if x)
            if card["voice_id"]:
                # prompt_tag has a stricter syntax in the compiler, so retain the
                # user's voice ID in prose and only use a safe tag when possible.
                tag = re.sub(r"[^a-z0-9]+", "-", card["voice_id"].casefold()).strip("-")[:64]
                if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", tag or ""):
                    asset["prompt_tag"] = tag
            selected_audio.append(asset_id)
        return {"subject_ids": active_subject_ids, "image_asset_ids": selected_images,
                "audio_asset_ids": selected_audio, "overview_kinds": overview_kinds,
                "overview_sources": overview_sources,
                "style_context_asset_ids": style_context,
                "voice_authority": "audio_primary" if selected_audio else "text_only",
                "image_limit": 9, "audio_limit": 3,
                "reference_strategy_version": REFERENCE_STRATEGY_VERSION}

    def attach_overview_asset(self, ident, kind, asset):
        production = self.get(ident)
        if kind not in OVERVIEW_CARD_KINDS:
            raise ValueError("Only character, wardrobe, prop and environment libraries accept overview images.")
        if asset.get("media_type") != "image":
            raise ValueError("A category overview must be a PNG, JPEG or WebP image.")
        safe_id(asset.get("id"))
        production["overview_asset_ids"][kind] = asset["id"]
        return {"production": self.save(production), "asset": asset}

    def attach_card_asset(self, ident, kind, card_id, asset):
        production = self.get(ident)
        if kind not in CARD_KINDS:
            raise ValueError("Unsupported production card kind.")
        card = next((item for item in production["cards"][kind] if item["id"] == safe_id(card_id)), None)
        if card is None:
            raise ValueError("Production card not found.")
        if kind == "voices" and asset.get("media_type") != "audio":
            raise ValueError("Voice cards accept WAV, MP3, M4A, FLAC or OGG audio only.")
        if kind != "voices" and asset.get("media_type") != "image":
            raise ValueError("Character, wardrobe, prop, environment and style cards accept images only.")
        safe_id(asset.get("id"))
        if asset["id"] not in card["asset_ids"]:
            card["asset_ids"].append(asset["id"])
        source = copy.deepcopy(self.load_project(production["source_project_id"]))
        characters, subjects = production["cards"]["characters"], source["subjects"]
        by_id = {subject["id"]: subject for subject in subjects}
        by_name = {subject["name"].strip().casefold(): subject for subject in subjects}
        for character in characters:
            subject = by_id.get(character.get("subject_id")) or by_name.get(character["name"].strip().casefold())
            if subject is None:
                subject = {"id": uid(), "name": character["name"], "description": character["description"], "asset_ids": []}
                subjects.append(subject)
            character["subject_id"] = subject["id"]
        owner_card = card if kind == "characters" else next((item for item in characters
            if item["id"] == card.get("character_card_id") or item["id"] == card.get("owner_card_id")), None)
        owner_subject = owner_card.get("subject_id") if owner_card else None
        attached = copy.deepcopy(asset)
        attached.update(enabled=False, role="reference_audio" if kind == "voices" else "reference_image",
                        semantic_role=CARD_SEMANTIC_ROLES[kind])
        if owner_subject:
            if kind == "props":
                attached["simple_owner_id"] = owner_subject
            else:
                attached.pop("simple_owner_id", None)
            subject = next(item for item in subjects if item["id"] == owner_subject)
            if attached["id"] not in subject["asset_ids"]:
                subject["asset_ids"].append(attached["id"])
        if not any(item["id"] == attached["id"] for item in source["assets"]):
            source["assets"].append(attached)
        check_project(source)
        self.save_project(source)
        return {"production": self.save(production), "asset": asset}

    def attach_generated_card_asset(self, ident, kind, card_id, asset):
        """Promote a finished image to this production only, newest image first.

        A production may share its Studio source project with other parts. Generated
        card images must not silently change that source or a reusable card set.
        """
        production = self.get(ident)
        if kind not in ("characters", "wardrobe", "props", "environments"):
            raise ValueError("Only visual production cards accept generated images.")
        card = next((item for item in production["cards"][kind] if item["id"] == safe_id(card_id)), None)
        if card is None:
            raise ValueError("Production card not found.")
        if not isinstance(asset, dict) or asset.get("media_type") != "image":
            raise ValueError("A generated production card asset must be an image.")
        asset_id = safe_id(asset.get("id"))
        card["asset_ids"] = [asset_id] + [value for value in card["asset_ids"] if value != asset_id]
        card["asset_ids"] = card["asset_ids"][:24]
        return {"production": self.save(production), "asset": asset}

    def auto_keyframe_suggestions(self, ident, limit=3):
        """Suggest sparse environment plates; never generate or upload here."""
        production = self.get(ident)
        environments = {card["id"]: card for card in production["cards"]["environments"]}
        previous_setting = ""
        suggestions = []
        for segment in production["segments"]:
            setting = segment["setting"].strip()
            setting_key = " ".join(setting.casefold().split())
            is_new_place = bool(setting_key and setting_key != previous_setting)
            if setting_key:
                previous_setting = setting_key
            if not is_new_place or segment.get("keyframe_asset_ids") or not segment.get("image_prompt", "").strip():
                continue
            selected = segment.get("card_selection", {}).get("environments", [])
            has_environment_reference = bool(production["overview_asset_ids"].get("environments")) or any(
                environments.get(card_id, {}).get("asset_ids") for card_id in selected)
            if has_environment_reference:
                continue
            style = (production.get("style_bible") or production.get("visual_style_custom") or "")[:700]
            prompt = ("Environment-only establishing keyframe for this exact scene. "
                      "Keep the spatial layout, lighting and color palette stable for video continuity. "
                      "No people, faces, duplicate characters, captions, signs or text. "
                      f"Setting: {setting[:700]}. "
                      f"Scene context: {segment['image_prompt'][:700]}. "
                      + (f"Series visual style: {style}." if style else ""))
            suggestions.append({"segment_id": segment["id"], "index": segment["index"],
                                "reason": "New location without a dedicated environment reference",
                                "prompt": prompt})
            if len(suggestions) >= limit:
                break
        return suggestions

    def set_image_run(self, ident, segment_id, run_id):
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment:
            raise ValueError("Production clip not found.")
        run_id = safe_id(run_id)
        segment["image_run_id"] = run_id
        if run_id not in segment["image_run_ids"]:
            segment["image_run_ids"].append(run_id)
            segment["image_run_ids"] = segment["image_run_ids"][-24:]
        return self.save(production)

    def attach_asset(self, ident, segment_id, asset):
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment or not isinstance(asset, dict) or not asset.get("id"):
            raise ValueError("Generated image asset is unavailable.")
        safe_id(asset["id"])
        segment["image_asset_id"] = asset["id"]
        if asset["id"] not in segment["keyframe_asset_ids"]:
            if len(segment["keyframe_asset_ids"]) >= MAX_SEGMENT_KEYFRAMES:
                raise ValueError(f"A clip can contain at most {MAX_SEGMENT_KEYFRAMES} dedicated keyframes.")
            segment["keyframe_asset_ids"].append(asset["id"])
        if segment.get("project_id"):
            project = self.load_project(segment["project_id"])
            if not any(a["id"] == asset["id"] for a in project["assets"]):
                attached = copy.deepcopy(asset)
                attached.update(enabled=True, role="context",
                                semantic_role=attached.get("semantic_role", "background"),
                                production_segment_id=segment["id"])
                project["assets"].append(attached)
                self.save_project(project)
        return self.save(production)

    def detach_asset(self, ident, segment_id, asset_id):
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        asset_id = safe_id(asset_id)
        if not segment or asset_id not in segment["keyframe_asset_ids"]:
            raise ValueError("This keyframe is not attached to the selected clip.")
        segment["keyframe_asset_ids"] = [value for value in segment["keyframe_asset_ids"] if value != asset_id]
        segment["image_asset_id"] = segment["keyframe_asset_ids"][-1] if segment["keyframe_asset_ids"] else None
        if segment.get("project_id"):
            project = self.load_project(segment["project_id"])
            project["assets"] = [item for item in project["assets"] if item.get("id") != asset_id]
            self.save_project(project)
        return self.save(production)

    def set_segment_prompt(self, ident, segment_id, prompt, source, seconds, project_id=None):
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment:
            raise ValueError("Production clip not found.")
        segment["video_prompt"] = _text(prompt, "compiled video prompt", 250000)
        segment["video_prompt_source"] = source if source in ("local_ai", "compiled") else "compiled"
        segment["prompt_seconds"] = _seconds(seconds)
        segment["prompt_updated_at"] = time.time()
        if project_id:
            segment["project_id"] = safe_id(project_id)
            segment["status"] = "ready"
            segment["source_hash"] = segment_hash(segment)
            segment["context_hash"] = production_context_hash(production)
            segment["stale_reasons"] = []
        return self.save(production)

    def set_video_run(self, ident, segment_id, run_id):
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment:
            raise ValueError("Production clip not found.")
        segment["last_video_run_id"] = safe_id(run_id)
        # A new render should become the default adopted take when it succeeds.
        # Keep every older run in history; only release a previous manual pin.
        segment["selected_video_run_id"] = None
        return self.save(production)

    def record_timing(self, ident, key, seconds):
        if key not in TIMING_KEYS:
            raise ValueError("Unsupported production timing metric.")
        production = self.get(ident)
        production["timings"][key] = _seconds(seconds)
        return self.save(production)

    def assert_active(self, ident):
        production = self.get(ident)
        if production["task_state"] == "paused":
            raise ValueError("This production is paused. Resume it before starting another task.")
        return production

    def has_inherited_clip_directions(self, production, project):
        """Recognise prompts made before materialisation stopped copying old clips."""
        try:
            source = self.load_project(production["source_project_id"])
        except Exception:
            return False
        if not source.get("production_link"):
            return False
        inherited = source.get("custom_instructions", "").strip()
        current = project.get("custom_instructions", "")
        # A production can legitimately reuse the exact same locked voice
        # block when the current clip has the same speakers as the Studio
        # project it was created from.  That block controls delivery only; it
        # contains no physical staging or previous-shot action and must not be
        # mistaken for leaked clip direction.  The old materialisation bug we
        # are guarding against copied arbitrary authored scene instructions.
        # Keep rejecting those, including any explicit clip revision request.
        if (inherited.startswith("VOICE DIRECTION — CURRENT CLIP ONLY")
                and "CLIP PROMPT REVISION REQUEST:" not in inherited):
            return False
        return bool(inherited and inherited in current)


def planning_chunks(story, limit=4500):
    """Keep each structured answer small enough for local-model token limits."""
    units = [x for x in re.split(r"(?<=\n)|(?<=[。！？!?；;])", story) if x.strip()]
    chunks, current = [], ""
    for unit in units:
        if current and len(current) + len(unit) > limit:
            chunks.append(current.strip())
            current = ""
        while len(unit) > limit:
            room = limit - len(current)
            current += unit[:room]
            chunks.append(current.strip())
            current, unit = "", unit[room:]
        current += unit
    if current.strip():
        chunks.append(current.strip())
    return chunks or [story]


def planning_card_catalog(production):
    character_names = {card["id"]: card["name"] for card in production["cards"]["characters"]}
    catalog = {}
    for kind in CARD_KINDS:
        rows = []
        for card in production["cards"][kind]:
            row = {"name": card["name"], "has_media": bool(card["asset_ids"])}
            if card["description"]:
                row["description"] = card["description"][:240]
            if kind == "styles" and card.get("image_analysis"):
                row["image_analysis"] = card["image_analysis"][:1200]
                row["priority"] = "highest_visual_authority"
            owner_id = card.get("character_card_id") if kind == "voices" else card.get("owner_card_id")
            if owner_id in character_names:
                row["character"] = character_names[owner_id]
            if kind == "voices" and card.get("voice_id"):
                row["voice_id"] = card["voice_id"]
            rows.append(row)
        catalog[kind] = rows
    return catalog


def planning_payload(production, story=None, chunk_index=1, chunk_total=1, previous_ending=""):
    source_story = story or production["brief"]
    timing = episode_timing_targets(production, chunk_index, chunk_total, source_story)
    locked_dialogue = locked_timed_dialogue(production, source_story)
    return json.dumps({
        "title": production["title"], "story_or_script": source_story,
        "project_output_language": PRODUCTION_LANGUAGES[production["language"]],
        "current_episode": production.get("current_episode", 1),
        "part": {"index": chunk_index, "total": chunk_total,
                 "previous_part_ending": previous_ending},
        "episode_timing": timing,
        "locked_source_dialogue": locked_dialogue,
        "visual_style_preset": production.get("visual_style_preset", "cinematic_realism"),
        "custom_visual_style": production.get("visual_style_custom", ""),
        "visual_style_bible": production["style_bible"],
        "style_source_priority": "style-card image analysis > custom visual style > visual style bible > named preset",
        "long_form_narrative_style": production.get("narrative_style", "cinematic"),
        "custom_narrative_style": production.get("narrative_style_custom", ""),
        "narrative_notes": production.get("narrative_notes", ""),
        "character_bible": production["character_bible"],
        "series_voice_style_verbatim": production.get("series_voice_style", ""),
        "available_asset_cards": planning_card_catalog(production),
        "manual_category_overviews": {kind: bool(production["overview_asset_ids"].get(kind))
                                      for kind in OVERVIEW_CARD_KINDS},
        "continuity_rules": production["continuity_notes"],
        "request": ("Plan this complete part in order. When episode_timing.timed_clip_groups is non-empty, preserve "
                    "each source-driven group in order and never merge across groups. You may subdivide a group only "
                    "when sequential action, dialogue timing or more than four visible identities make one generation clip unsafe; "
                    "the child durations must cover the parent group exactly. Otherwise normally return exactly "
                    "episode_timing.recommended_clip_count clips, "
                    "but use another count inside episode_timing.feasible_clip_count only when the story genuinely requires it. "
                    "The sum of clip durations must equal episode_timing.part_target_seconds. Derive individual 5-15 second "
                    "durations from dialogue and action rather than making them mechanically uniform. Prefer at most four visible "
                    "named identities per clip. Fill cast_timeline for every clip: visible_start and visible_end are exact boundary "
                    "states; enters and exits are physical changes during this clip; offscreen and mentioned_only must never be "
                    "depicted. A character who exited stays absent from later clips until a later cast_timeline.enters explicitly "
                    "brings that character back. Set transition_mode to continuous only for truly unbroken time/place/action; use "
                    "hard_cut, matched_cut, time_jump, state_change or insert for discontinuities. Make card_selection.characters "
                    "the exact union of visible_start, visible_end, enters and exits. In each locked_source_dialogue group, source_dialogue is the "
                    "complete authored line roster. When requires_translation is false, copy dialogue verbatim. When it is true, translate each "
                    "source_dialogue text once into project_output_language while preserving speaker, count and order; never consolidate, omit, "
                    "reassign or invent a line. For each clip copy only "
                    "exact available card names into card_selection. Start continuously after previous_part_ending when present.")
    }, ensure_ascii=False, indent=2)


def episode_planning_payload(production, start_index, count, previous_ending=""):
    return json.dumps({
        "title": production["title"],
        "source_story_or_screenplay": production["brief"],
        "project_output_language": PRODUCTION_LANGUAGES[production["language"]],
        "requested_episode_count": production["episode_count"],
        "target_minutes_per_episode": production["episode_minutes"],
        "requested_batch": {"start_episode": start_index + 1, "count": count,
                            "previous_batch_ending": previous_ending},
        "visual_style": {"preset": production.get("visual_style_preset", "cinematic_realism"),
                         "custom": production.get("visual_style_custom", ""),
                         "notes": production["style_bible"],
                         "source_priority": "style-card image analysis > custom text > named preset"},
        "long_form_narrative_style": {"preset": production.get("narrative_style", "cinematic"),
                                      "custom": production.get("narrative_style_custom", ""),
                                      "notes": production.get("narrative_notes", "")},
        "available_character_cards": [card["name"] for card in production["cards"]["characters"]],
        "character_bible": production["character_bible"],
        "film_wide_continuity_rules": production["continuity_notes"],
        "request": f"Return exactly {count} consecutive episodes, numbered conceptually from {start_index + 1}. Include the complete cast for each episode.",
    }, ensure_ascii=False, indent=2)


def card_planning_payload(production, story=None, chunk_index=1, chunk_total=1):
    return json.dumps({
        "title": production["title"],
        "source_story_or_screenplay": story or production["brief"],
        "part": {"index": chunk_index, "total": chunk_total},
        "project_output_language": PRODUCTION_LANGUAGES[production["language"]],
        "genre_and_visual_context": {
            "visual_style_preset": production.get("visual_style_preset", "cinematic_realism"),
            "custom_visual_style": production.get("visual_style_custom", ""),
            "visual_style_bible": production.get("style_bible", ""),
            "narrative_style": production.get("narrative_style", "cinematic"),
            "narrative_notes": production.get("narrative_notes", ""),
        },
        "existing_character_bible": production.get("character_bible", ""),
        "existing_series_voice_style": production.get("series_voice_style", ""),
        "existing_cards": {
            kind: [{"name": card["name"], "description": card["description"],
                    "notes": card["notes"], "has_reference_media": bool(card["asset_ids"])}
                   for card in production["cards"][kind]]
            for kind in CARD_KINDS
        },
        "merge_policy": "Fill blank text and add missing cards only. Never rewrite user text, rename cards or remove media.",
    }, ensure_ascii=False, indent=2)
