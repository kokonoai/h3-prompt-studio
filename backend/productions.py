"""Long-form production planning built on ordinary H3 Studio projects."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from .projects import PROMPT_VERSIONS, atomic_json, check_project, safe_id, shot, uid
from .video_workflows import REF8_WORKFLOW_ID, ref8_recipe_settings

MIN_SECONDS, PLANNED_MIN_SECONDS, DEFAULT_CLIP_SECONDS = 4, 5, 10
MAX_SECONDS, MAX_SEGMENTS, MAX_EPISODES = 15, 64, 100
MAX_SEGMENT_KEYFRAMES = 12
REFERENCE_STRATEGY_VERSION = 9
CAST_TIMELINE_VERSION = 1
STORY_CONTRACT_VERSION = 1
TIMING_KEYS = ("episode_plan_seconds", "storyboard_plan_seconds", "merge_seconds")
AUTOMATION_STATUSES = ("idle", "running", "retrying", "paused", "needs_attention", "completed")
AUTOMATION_STAGES = ("idle", "scan", "prompts", "videos", "quality", "merge", "paused", "completed")
CARD_KINDS = ("characters", "wardrobe", "props", "environments", "voices", "styles")
SELECTABLE_CARD_KINDS = ("characters", "wardrobe", "props", "environments", "voices")
OVERVIEW_CARD_KINDS = ("characters", "wardrobe", "props", "environments")
CAST_TIMELINE_KEYS = ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")
TRANSITION_MODES = ("continuous", "matched_cut", "hard_cut", "time_jump", "state_change", "insert")
# These failures describe an unsafe *relationship between two adjacent clips*,
# not a broken story beat.  The deterministic repair is therefore editorial:
# keep both clips and their exact cast/story/dialogue, but stop treating the
# boundary as one continuous MMH3 take.
AUTO_HARD_CUT_CONTINUITY_CODES = frozenset({
    "sudden_character_addition", "sudden_character_disappearance",
    "remote_became_physical", "spatial_conflict", "movement_direction_conflict",
    "eyeline_conflict", "prop_changed_hands", "prop_state_jump",
    "wardrobe_jump", "axis_crossing", "invalid_mmh3_continuation",
})
SHOT_ROLES = ("master", "reaction", "close_up", "insert", "establishing", "over_shoulder", "cutaway")
SHOT_SIZES = ("extreme_wide", "wide", "medium", "medium_close", "close_up", "extreme_close_up")
EDIT_REASONS = ("dialogue_reaction", "action_match", "eyeline_match", "information_reveal",
                "time_jump", "scene_change", "continuity", "emphasis")
DEVICE_VIEW_MODES = ("auto", "performance", "screen", "front_camera", "phone_to_ear", "remote_panel")
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
                         "cast_timeline", "transition_mode", "source_refs", "continuity_state", "shot_contract"],
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
                "device_view": {"type": "string", "enum": list(DEVICE_VIEW_MODES)},
                "source_refs": {
                    "type": "object", "additionalProperties": False,
                    "required": ["scene_ids", "paragraph_ids", "dialogue_ids", "event_ids"],
                    "properties": {
                        key: {"type": "array", "maxItems": 64,
                              "items": {"type": "string", "maxLength": 32}}
                        for key in ("scene_ids", "paragraph_ids", "dialogue_ids", "event_ids")
                    }},
                "continuity_state": {
                    "type": "object", "additionalProperties": False,
                    "required": ["opening_state", "ending_state", "positions_start", "positions_end",
                                 "prop_holders_start", "prop_holders_end", "mmh3_eligible"],
                    "properties": {
                        "opening_state": {"type": "string", "maxLength": 1200},
                        "ending_state": {"type": "string", "maxLength": 1200},
                        "positions_start": {"type": "array", "maxItems": 16, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["character", "position", "facing", "movement_direction",
                                         "eyeline_target", "eyeline_direction"],
                            "properties": {
                                "character": {"type": "string", "maxLength": 120},
                                "position": {"type": "string", "maxLength": 160},
                                "facing": {"type": "string", "maxLength": 120},
                                "movement_direction": {"type": "string", "maxLength": 120},
                                "eyeline_target": {"type": "string", "maxLength": 120},
                                "eyeline_direction": {"type": "string", "maxLength": 120}}}},
                        "positions_end": {"type": "array", "maxItems": 16, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["character", "position", "facing", "movement_direction",
                                         "eyeline_target", "eyeline_direction"],
                            "properties": {
                                "character": {"type": "string", "maxLength": 120},
                                "position": {"type": "string", "maxLength": 160},
                                "facing": {"type": "string", "maxLength": 120},
                                "movement_direction": {"type": "string", "maxLength": 120},
                                "eyeline_target": {"type": "string", "maxLength": 120},
                                "eyeline_direction": {"type": "string", "maxLength": 120}}}},
                        "prop_holders_start": {"type": "array", "maxItems": 24, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["prop", "holder", "state"],
                            "properties": {"prop": {"type": "string", "maxLength": 120},
                                           "holder": {"type": "string", "maxLength": 120},
                                           "state": {"type": "string", "maxLength": 240}}}},
                        "prop_holders_end": {"type": "array", "maxItems": 24, "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["prop", "holder", "state"],
                            "properties": {"prop": {"type": "string", "maxLength": 120},
                                           "holder": {"type": "string", "maxLength": 120},
                                           "state": {"type": "string", "maxLength": 240}}}},
                        "mmh3_eligible": {"type": "boolean"},
                    }},
                "shot_contract": {
                    "type": "object", "additionalProperties": False,
                    "required": ["role", "shot_size", "opening_composition", "ending_composition",
                                 "camera_axis", "allow_axis_cross", "edit_reason", "relation_previous",
                                 "preserve_from_previous", "must_change"],
                    "properties": {
                        "role": {"type": "string", "enum": list(SHOT_ROLES)},
                        "shot_size": {"type": "string", "enum": list(SHOT_SIZES)},
                        "opening_composition": {"type": "string", "maxLength": 1000},
                        "ending_composition": {"type": "string", "maxLength": 1000},
                        "camera_axis": {"type": "string", "maxLength": 500},
                        "allow_axis_cross": {"type": "boolean"},
                        "edit_reason": {"type": "string", "enum": list(EDIT_REASONS)},
                        "relation_previous": {"type": "string", "enum": list(TRANSITION_MODES)},
                        "preserve_from_previous": {"type": "string", "maxLength": 1200},
                        "must_change": {"type": "string", "maxLength": 1200},
                    }},
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

SOURCE COVERAGE CONTRACT
- The request supplies a source_manifest with stable scene, paragraph, dialogue and event IDs. Every ID must be assigned to exactly one clip in source_refs, except a scene ID may repeat across adjacent clips belonging to that scene.
- Keep paragraph_ids, dialogue_ids and event_ids in their authored order. Never omit, duplicate, reassign or reorder them. source_refs is an editorial binding, not prose to show or speak.
- The dialogue_ids selected for a clip must correspond exactly to that clip's structured dialogue, with the same speaker and order. A heading can appear in scene_ids or paragraph_ids but never in dialogue_ids or structured dialogue.
- A clip may cover several consecutive source items, but it may not take an item from after an item assigned to a later clip.

ADJACENT CONTINUITY CONTRACT
- Fill continuity_state for every clip. opening_state and ending_state name the concrete visible state, not theme or mood alone.
- positions_start/end record only physically present characters, using stable screen-space positions (left, centre, right, foreground/background), facing, movement direction, eyeline target and eyeline direction. Do not put screen, memory or off-screen identities there.
- prop_holders_start/end record important handheld or continuity-critical props. Use holder="environment" when placed down and holder="none" only when deliberately absent. state records visible continuity facts such as phone orientation, screen direction/on-off state, open/closed state, or the hand holding it; use an empty string only when no visible state matters.
- mmh3_eligible is true only when this clip can safely inherit the immediately preceding saved motion/audio tail: uninterrupted time and place, compatible opening cast/positions/props, and no insert, time jump, state change, remote panel or memory transition.
- transition_mode is the cut relation to the previous clip. The first clip is hard_cut. Do not mark a clip continuous merely because the story is related.
- Fill shot_contract without asking the user. Choose the clip's editorial responsibility, shot size, opening/ending composition, camera axis, whether a motivated axis crossing is allowed, the edit reason, relation to the previous clip, what must be preserved and what must visibly change.
- Avoid accidental jump cuts: adjacent clips should not repeat nearly identical composition and performance unless an intentional matched cut or continuous action requires it. For dialogue, keep reciprocal eyelines and the established axis. For inserts/cutaways, make the information purpose explicit and do not duplicate the preceding action.

COMPACT STRUCTURED OUTPUT
- Keep the JSON production-usable but concise so local models can finish it. Use a short title; one or two sentences for story; one sentence for setting; one to three sentences for action; one sentence for ending; one short clause for duration_reason; and one or two sentences for image_prompt.
- Never repeat the character bible, voice cards, visual-style bible, card descriptions or these instructions inside a segment. Refer to cards only by their exact names in card_selection.
- Authored dialogue is the exception: preserve every required line verbatim (or faithfully translated when requested), even when that makes a segment longer.

ENSEMBLE GENERATION SAFETY
- Prefer 1-4 visible named identities in one generation clip. When a source block requires more, subdivide at a causal action boundary so each adjacent clip preserves story order and total duration. Do not omit required characters merely to satisfy this preference.
- Give every visible character one stable physical instance and a distinct screen region. Characters with similar palette, size or silhouette need different body geometry and signature markers stated in setting/action/image_prompt. Never solve crowding by duplicating, mirroring, blending, replacing or swapping identities.
- card_selection.characters is the exact on-screen roster for that clip, not everyone mentioned in surrounding continuity or an editorial summary. Off-screen, already-exited, next-clip and merely discussed characters do not belong in that array.
- Fill cast_timeline for every clip. visible_start and visible_end are the physical cast visible at those exact boundaries. enters and exits are narrative entrances/departures during the clip, not ordinary reframing or temporary occlusion. offscreen contains heard or spatially present characters who never become visible in this clip. mentioned_only contains characters discussed only in summaries, memories, prior/future events or continuity notes.
- A character removed, transported, reset, teleported or sent to another location belongs in exits for that clip and stays physically absent from later clips until a later clip explicitly lists that character in enters. Never put an exited character back into visible_start/visible_end/card_selection merely because prose mentions the character or reports its distant sound.
- Walking through a doorway into the next adjoining shot, climbing onward, leaving the current framing, a camera cut, a dissolve or temporary occlusion is NOT a persistent narrative exit. Do not place such continuing performers in exits merely because they leave one composition. Keep them available to the next sequential clip.
- A montage or time-compressed beat that explicitly shows named performers acting must list those performers in card_selection.characters and in visible_start/visible_end/enters/exits as appropriate. Never demote visibly acting performers to mentioned_only.
- card_selection.characters must equal the union of cast_timeline.visible_start, visible_end, enters and exits. It contains an exiting character because that character needs a reference while still visible, but never contains offscreen or mentioned_only characters.
- A person seen only inside a phone, monitor, recorded video or remote-call panel is not physically present in the local setting: exclude that person from card_selection.characters and every visible/enter/exit bucket, place the person in offscreen, and state the bounded screen depiction explicitly in action/image_prompt (for example "Besi's face appears only inside the phone screen"). The application will bind the identity reference to that display plane without creating a second body in the room.
- A person who is merely missed, remembered, discussed or mentally addressed belongs in mentioned_only and must not be visualised as standing beside the thinker. Express ordinary longing through the present actor's performance and an established prop. Only when the source explicitly requires a visible memory/dream may action/image_prompt request one clearly non-diegetic bounded memory image.
- Do not combine typing/sending, a recipient's remote reaction and an imagined encounter as one spatial composition. Prefer adjacent causal clips: local phone action; remote reaction or explicit memory insert; return to the local end state. Preserve total target duration.
- Every phone/device clip must choose one physically possible view and state it in action/image_prompt. PERFORMANCE VIEW shows the holder's face while the screen faces the holder and the audience sees only the device back/edge. SCREEN VIEW is an insert or over-the-shoulder composition aligned to the display, so the audience sees the screen while only the holder's hands, shoulder or partial profile is visible. FRONT-CAMERA VIEW uses the phone camera's viewpoint and does not show the phone screen. PHONE-TO-EAR keeps the screen hidden and inactive. Never show an unobstructed frontal face and a square-on readable phone screen at the same time; never use a transparent, mirrored, floating or double-sided display.
- If both a readable phone display and the holder's facial reaction are dramatically important, split them into adjacent generation clips or choose one as the visible priority. Do not ask one generated frame to establish both incompatible views.
- device_view is an optional director override: auto, performance, screen, front_camera, phone_to_ear or remote_panel. Normally return auto and let the application infer a safe view. Use a specific value only when the source clearly requires that geometry; never use it to invent a device absent from the story.
- transition_mode describes the editorial relationship to the previous clip: continuous only for unbroken time/place/action suitable for saved-motion continuation; matched_cut for a deliberate same-scene reframing; hard_cut for an ordinary new shot or location; time_jump for montage or elapsed time; state_change for reset, teleport, transformation or discontinuous world-state change; insert for a detail/cutaway. The first clip is hard_cut.
- transition_mode and shot_contract.relation_previous describe the edit AT THE BOUNDARY before this clip. Do not repeat that boundary as an internal "cut to", match cut, transition or second setup inside story, setting, action, ending or image_prompt. Those fields describe only the new clip's side of the boundary and one uninterrupted filmable camera setup. If the source truly requires another setup, allocate it to an adjacent generation clip while preserving source order, locked dialogue and total duration.

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


_INTER_SHOT_SECTION_HEADING = re.compile(
    r"(?im)^[ \t]*(?:"
    r"#{1,2}[ \t]+\S[^\r\n]*|"
    r"(?:part|chapter|act|section)[ \t]+[A-Za-z0-9一二三四五六七八九十._-]+[^\r\n]*|"
    r"第[ \t]*[A-Za-z0-9一二三四五六七八九十._-]+[ \t]*(?:部分|章|幕|节|節)[^\r\n]*"
    r")$"
)


def _trim_inter_shot_section_metadata(body):
    """Keep the next section header out of the preceding authored shot.

    Shot timecodes are the narrowest editorial authority.  A storyboard often
    places a new ``Part`` heading, its duration, cast roster and production
    notes after the last shot of the previous part but before the next shot
    heading.  Because shot matches delimit the raw body, those notes otherwise
    become fake plot events owned by the previous clip.
    """
    heading = _INTER_SHOT_SECTION_HEADING.search(body)
    if heading is None:
        return body.strip()
    cut = heading.start()
    prefix = body[:cut]
    separator = re.search(
        r"(?ms)(?:^|\n)[ \t]*(?:-{3,}|_{3,}|\*{3,})[ \t]*(?:\r?\n[ \t]*)*$",
        prefix,
    )
    if separator is not None:
        cut = separator.start()
    return body[:cut].strip()


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
        body = story[match.end():text_end]
        if shot_matches:
            body = _trim_inter_shot_section_metadata(body)
        else:
            body = body.strip()
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


def timed_group_story(group):
    """Rebuild one authored timing block without losing its timecode.

    Storyboard planning sends explicit source groups to the local model one at
    a time.  Keeping the header is essential: it lets the per-call schema,
    timing payload and locked-dialogue roster describe the same source block.
    """
    start = int(group["start"])
    end = int(group["end"])
    return (f"{start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d}\n"
            f"{str(group['text']).strip()}")


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


_EDITORIAL_SPEAKER_LABEL = re.compile(
    r"^(?:part|scene|shot|segment|episode|act|chapter|section|beat|unit)\s*(?:no\.?\s*)?\d+[a-z]?$|"
    r"^第\s*[一二三四五六七八九十百零〇0-9]+\s*(?:段|场|場|镜|鏡|集|幕|章|节|節|部分)$|"
    r"^(?:パート|シーン|ショット|セグメント|エピソード|幕|章)\s*[0-9一二三四五六七八九十百零〇]+$",
    re.IGNORECASE,
)


def editorial_speaker_label(value):
    """Return true for screenplay headings that cannot be dialogue owners.

    Markdown headings such as ``## Part 5: “I Love You”`` resemble an inline
    speaker cue.  Treating them as speech creates a synthetic visible person
    named ``Part 5`` and makes printed prop text audible.  Keep the rule narrow
    so ordinary character names remain untouched.
    """
    value = _strip_script_markdown(str(value or "")).strip("*_#- ")
    return bool(_EDITORIAL_SPEAKER_LABEL.fullmatch(value))


def _dialogue_signal_count(text):
    """Count strong cue+quote signals independently from the main parser."""
    rows = [raw.strip() for raw in str(text or "").splitlines() if raw.strip()]
    count = 0
    for index, raw in enumerate(rows):
        marker = _speaker_marker(raw)
        if not marker:
            continue
        speaker, inline = marker
        if editorial_speaker_label(speaker):
            continue
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
            if editorial_speaker_label(speaker):
                pending = None
                continue
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


_SOURCE_SCENE_HEADING = re.compile(
    r"^(?:int\.?|ext\.?|int\.?/ext\.?|i/e\.?|scene|shot|chapter|episode|act|part)\b|"
    r"^(?:场景|場景|第\s*[一二三四五六七八九十百零〇0-9]+\s*(?:场|場|幕|章|节|節|段))|"
    r"^(?:シーン|ショット|第\s*[0-9一二三四五六七八九十百零〇]+\s*(?:話|幕|章))",
    re.IGNORECASE,
)
_SOURCE_TIMECODE_ONLY = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?\s*(?:[-–—~至到]\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)?\s*$")


def _source_blocks(text):
    """Return auditable source rows without separating a cue from its quote."""
    rows = [row.strip() for row in str(text or "").splitlines() if row.strip()]
    blocks, index = [], 0
    while index < len(rows):
        row = rows[index]
        marker = _speaker_marker(row)
        if marker and not editorial_speaker_label(marker[0]):
            inline = _strip_script_markdown(marker[1])
            if not inline and index + 1 < len(rows):
                following = _strip_script_markdown(rows[index + 1])
                if following and following[0] in _DIALOGUE_OPENERS:
                    blocks.append(row + "\n" + rows[index + 1])
                    index += 2
                    continue
        blocks.append(row)
        index += 1
    return blocks


def source_manifest_for_text(text, chunk_index=1):
    """Build stable source IDs used by planning, UI review and render admission."""
    prefix = f"C{int(chunk_index):02d}"
    scenes, paragraphs, dialogue, events = [], [], [], []
    scene_number, current_scene = 1, f"{prefix}-S001"
    scenes.append({"id": current_scene, "title": "Scene 1"})
    for block in _source_blocks(text):
        clean = _strip_script_markdown(block).strip()
        heading = bool(
            _SOURCE_SCENE_HEADING.search(clean) or _SOURCE_TIMECODE_ONLY.fullmatch(clean) or
            editorial_speaker_label(clean.split(":", 1)[0].strip()))
        if heading:
            if paragraphs or scenes[0]["title"] != "Scene 1":
                scene_number += 1
                current_scene = f"{prefix}-S{scene_number:03d}"
                scenes.append({"id": current_scene, "title": clean[:240]})
            else:
                scenes[0]["title"] = clean[:240]
        paragraph_id = f"{prefix}-P{len(paragraphs) + 1:03d}"
        parsed = script_dialogue(block)
        paragraphs.append({"id": paragraph_id, "scene_id": current_scene,
                           "kind": "heading" if heading else "dialogue" if parsed else "action",
                           "text": block[:2000]})
        for line in parsed:
            dialogue.append({"id": f"{prefix}-D{len(dialogue) + 1:03d}",
                             "scene_id": current_scene, "paragraph_id": paragraph_id,
                             "speaker": line["speaker"], "text": line["text"]})
        if not heading and not parsed:
            events.append({"id": f"{prefix}-E{len(events) + 1:03d}",
                           "scene_id": current_scene, "paragraph_id": paragraph_id,
                           "text": block[:2000]})
    return {"chunk": int(chunk_index), "source_hash": _hash(str(text or "")),
            "scenes": scenes, "paragraphs": paragraphs, "dialogue": dialogue, "events": events}


def production_source_manifest(production, story=None):
    """Derive the complete current-episode manifest using planner boundaries."""
    story = story if story is not None else current_episode_story(production)
    timed = timed_clip_groups(story)
    pieces = ([timed_group_story(group) for group in timed]
              if timed else storyboard_planning_chunks(story))
    chunks = [source_manifest_for_text(piece, index + 1) for index, piece in enumerate(pieces)]
    return {"version": STORY_CONTRACT_VERSION, "story_hash": _hash(story), "chunks": chunks}


SOURCE_REF_KEYS = ("scene_ids", "paragraph_ids", "dialogue_ids", "event_ids")


def normalise_source_refs(value):
    if value is None:
        return {key: [] for key in SOURCE_REF_KEYS}
    if not isinstance(value, dict) or set(value) - set(SOURCE_REF_KEYS):
        raise ValueError("source_refs contains unsupported fields.")
    result = {}
    for key in SOURCE_REF_KEYS:
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > 64:
            raise ValueError(f"source_refs.{key} contains too many source IDs.")
        result[key] = list(dict.fromkeys(
            _text(row, f"source_refs.{key}", 32) for row in rows
            if isinstance(row, str) and row.strip()))
    return result


def normalise_continuity_state(value, segment=None):
    """Keep only compact, filmable state needed between adjacent clips."""
    value = value if isinstance(value, dict) else {}
    segment = segment or {}

    def positions(key):
        result = []
        for row in value.get(key, []) if isinstance(value.get(key, []), list) else []:
            if not isinstance(row, dict):
                continue
            character = _text(row.get("character"), f"continuity_state.{key}.character", 120)
            if character:
                result.append({"character": character,
                               "position": _text(row.get("position"), f"continuity_state.{key}.position", 160),
                               "facing": _text(row.get("facing"), f"continuity_state.{key}.facing", 120),
                               "movement_direction": _text(row.get("movement_direction"),
                                                           f"continuity_state.{key}.movement_direction", 120),
                               "eyeline_target": _text(row.get("eyeline_target"),
                                                       f"continuity_state.{key}.eyeline_target", 120),
                               "eyeline_direction": _text(row.get("eyeline_direction"),
                                                          f"continuity_state.{key}.eyeline_direction", 120)})
        return result[:16]

    def holders(key):
        result = []
        for row in value.get(key, []) if isinstance(value.get(key, []), list) else []:
            if not isinstance(row, dict):
                continue
            prop = _text(row.get("prop"), f"continuity_state.{key}.prop", 120)
            if prop:
                result.append({"prop": prop,
                               "holder": _text(row.get("holder"), f"continuity_state.{key}.holder", 120,
                                               "none") or "none",
                               "state": _text(row.get("state"), f"continuity_state.{key}.state", 240)})
        return result[:24]

    eligible = value.get("mmh3_eligible", segment.get("transition_mode") == "continuous")
    if type(eligible) is not bool:
        eligible = False
    return {
        "opening_state": _text(value.get("opening_state"), "continuity opening state", 1200,
                               segment.get("setting", "")),
        "ending_state": _text(value.get("ending_state"), "continuity ending state", 1200,
                              segment.get("ending", "")),
        "positions_start": positions("positions_start"),
        "positions_end": positions("positions_end"),
        "prop_holders_start": holders("prop_holders_start"),
        "prop_holders_end": holders("prop_holders_end"),
        "mmh3_eligible": eligible,
    }


def normalise_shot_contract(value, segment=None):
    value = value if isinstance(value, dict) else {}
    segment = segment or {}
    relation = value.get("relation_previous", segment.get("transition_mode", "hard_cut"))
    if relation not in TRANSITION_MODES:
        relation = "hard_cut"
    role = value.get("role", "master")
    if role not in SHOT_ROLES:
        role = "master"
    size = value.get("shot_size", "medium")
    if size not in SHOT_SIZES:
        size = "medium"
    reason = value.get("edit_reason", "continuity")
    if reason not in EDIT_REASONS:
        reason = "continuity"
    allow_cross = value.get("allow_axis_cross", False)
    if type(allow_cross) is not bool:
        allow_cross = False
    return {
        "role": role, "shot_size": size,
        "opening_composition": _text(value.get("opening_composition"), "opening composition", 1000,
                                     segment.get("setting", "")),
        "ending_composition": _text(value.get("ending_composition"), "ending composition", 1000,
                                    segment.get("ending", "")),
        "camera_axis": _text(value.get("camera_axis"), "camera axis", 500),
        "allow_axis_cross": allow_cross, "edit_reason": reason,
        "relation_previous": relation,
        "preserve_from_previous": _text(value.get("preserve_from_previous"), "preserved edit state", 1200),
        "must_change": _text(value.get("must_change"), "required edit change", 1200),
    }


def reconcile_transition_contract(transition_mode, relation_previous, index):
    """Collapse the planner's duplicate cut fields into one safe authority.

    ``transition_mode`` controls continuity checks and MMH3 admission, while
    the shot contract carries the same relation for editorial display. Local
    models occasionally use ``continuous`` there merely to mean continuous
    story time. Never promote that ambiguity to saved-motion continuation.
    Conversely, a specific discontinuous edit is more informative than the
    generic ``hard_cut`` default and is safe to preserve.
    """
    transition = transition_mode if transition_mode in TRANSITION_MODES else "hard_cut"
    relation = relation_previous if relation_previous in TRANSITION_MODES else transition
    if int(index) == 0:
        return "hard_cut"
    if transition == relation:
        return transition
    specific_cuts = {"matched_cut", "time_jump", "state_change", "insert"}
    if transition == "hard_cut" and relation in specific_cuts:
        return relation
    return transition


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


def storyboard_output_token_budget(clip_count):
    """Reserve enough JSON output without exceeding local client limits.

    LM Studio and the OpenAI-compatible local client both cap one structured
    response at 4,096 output tokens.  Larger values fail before generation and
    used to make a normal AI plan look like a successful heuristic fallback.
    Long episodes are already planned in bounded source parts, so keep each
    part inside the real transport limit and let source-contract validation
    retry an incomplete part.
    """
    count = max(1, int(clip_count or 1))
    return min(4096, max(2400, 1800 + count * 1300))


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
    # A long authored screenplay is planned one explicit timing block per
    # local-model call.  For those calls the block's own duration is the part
    # target; dividing the episode target by the number of calls would turn a
    # 12-second authored block into an unrelated nominal 10-second request.
    if timed_groups:
        part = sum(group["duration"] for group in timed_groups)
        minimum = max(1, math.ceil(part / MAX_SECONDS))
        maximum = max(minimum, part // PLANNED_MIN_SECONDS)
    recommended = recommended_clip_count(part)
    if timed_groups and minimum <= len(timed_groups) <= min(maximum, MAX_SEGMENTS):
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
        ] if timed_groups else [],
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
        ("title", "language", "brief", "visual_style_preset", "visual_style_custom", "narrative_style", "narrative_style_custom", "narrative_notes",
         "style_bible", "character_bible", "continuity_notes", "series_voice_style", "cards", "overview_asset_ids",
         "video_aspect_ratio", "video_resolution", "video_quality", "video_steps",
         "current_episode", "episodes")}
    # Prompt-renderer behavior is part of the prepared clip contract. Bump this
    # when a correction requires existing saved prompts to be regenerated;
    # source projects, cards, media and completed videos remain untouched.
    context["production_prompt_renderer"] = "source-and-adjacent-continuity-v7"
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
    # ``None`` means this is a legacy clip which never authored a timeline, so
    # selected characters are the safest backward-compatible fallback.  An
    # explicit all-empty timeline is different: it is a valid contract for a
    # display-only call, a memory insert, or a clip containing only off-screen
    # audio.  Re-filling that explicit empty value makes remote identities
    # physical again and causes a freshly rebuilt prompt to become stale as
    # soon as it is saved.
    legacy_missing = value is None
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
    if legacy_missing and not any(result.values()):
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


def voice_character_aliases(production):
    """Return unambiguous voice-card labels bound to their character card.

    Local planners sometimes emit a voice ID (``RICK_V1``) or voice-card name
    in the dialogue speaker field.  Those values describe *how* the owning
    character sounds; they are never additional visual identities.  Keep this
    mapping separate from screenplay aliases so voice labels are only accepted
    where a speaker is being resolved.
    """
    cards = production.get("cards", {})
    characters = {card.get("id"): card for card in cards.get("characters", []) if card.get("id")}
    owners = {}
    ambiguous = set()
    for voice in cards.get("voices", []):
        character = characters.get(voice.get("character_card_id"))
        if character is None:
            continue
        for value in (voice.get("name"), voice.get("voice_id")):
            key = str(value or "").strip().casefold()
            if not key:
                continue
            previous = owners.get(key)
            if previous is not None and previous.get("id") != character.get("id"):
                ambiguous.add(key)
            else:
                owners[key] = character
    return {key: card for key, card in owners.items() if key not in ambiguous}


_SPLIT_LAYOUT_CUE = re.compile(
    r"\b(?:split[- ]screen|split[- ]panel|remote[- ]call panel|split locations?|"
    r"separate (?:rooms?|locations?|video panels?)|respective (?:rooms?|locations?)|"
    r"each (?:in|inside) (?:his|her|their) own (?:room|location)|"
    r"alternating matching (?:medium )?close[- ]?ups?)\b|"
    r"分屏|分割画面|画面分割|分别位于不同|分別位於不同|各自的房间|各自的房間|"
    r"交替匹配.{0,8}(?:近景|特写|特寫)|スプリットスクリーン|別々の(?:部屋|場所)|"
    r"交互のマッチング(?:クローズアップ|ミディアムショット)",
    re.IGNORECASE,
)
_NEGATED_SPLIT_LAYOUT_CUE = re.compile(
    r"\b(?:no|without|avoid(?:ing)?|never\s+use|not\s+(?:a|using))\s+(?:a\s+)?"
    r"(?:split[- ]screen|split[- ]panel|remote[- ]call panel)\b|"
    r"(?:不要|不使用|禁止|避免|无|無).{0,8}(?:分屏|分割画面|画面分割)|"
    r"(?:スプリットスクリーン|分割画面)(?:なし|を使わない|禁止)",
    re.IGNORECASE,
)


def _split_layout_requested(text):
    """Return true only for an affirmative multi-panel composition request."""
    without_negative_locks = _NEGATED_SPLIT_LAYOUT_CUE.sub("", str(text or ""))
    return bool(_SPLIT_LAYOUT_CUE.search(without_negative_locks))


_REMOTE_SEPARATION_CUE = re.compile(
    r"\b(?:only\s+(?:be\s+)?together\s+online|online[- ]only|long[- ]distance|"
    r"remain\s+physically\s+separate|separate\s+(?:physical\s+)?locations?|"
    r"never\s+(?:occupy|share|enter|meet\s+in)\s+(?:the\s+)?(?:same\s+)?(?:physical\s+)?(?:room|space|location)|"
    r"do\s+not\s+(?:visit|touch|meet|share\s+(?:a\s+)?physical\s+location))\b|"
    r"只能.{0,12}(?:线上|線上|网络|網路)|异地|異地|保持物理分离|保持物理分離|"
    r"不(?:得|能|会|會|可).{0,18}(?:同处|同處|同一个房间|同一個房間|共享.{0,6}(?:空间|空間|地点|地點))|"
    r"オンラインでのみ|遠距離|物理的に離れ|同じ(?:部屋|場所).{0,12}(?:いない|入らない)",
    re.IGNORECASE,
)
_LOCATION_CUE = (
    r"room|apartment|home|house|bedroom|office|studio|desk|workspace|kitchen|hallway|"
    r"房间|房間|公寓|家中|卧室|臥室|办公室|辦公室|工作室|书桌|書桌|厨房|廚房|走廊|"
    r"部屋|アパート|自宅|寝室|オフィス|スタジオ|机|台所|廊下"
)


def remote_separated_pairs(production, aliases_by_card=None):
    """Extract canon pairs that must never share a physical location."""
    cards = production.get("cards", {}).get("characters", [])
    aliases_by_card = aliases_by_card or character_aliases(production)
    canon_parts = [production.get("brief", ""), production.get("continuity_notes", "")]
    canon_parts.extend(episode.get("continuity_notes", "")
                       for episode in production.get("episodes", []) if isinstance(episode, dict))
    canon = "\n".join(str(part or "") for part in canon_parts)
    if not _REMOTE_SEPARATION_CUE.search(canon):
        return set()
    pairs = set()
    contexts = [part for part in re.split(r"[\r\n]+|(?<=[.!?。！？])\s+", canon)
                if _REMOTE_SEPARATION_CUE.search(part)]
    for context in contexts:
        mentioned = []
        for card in cards:
            aliases = aliases_by_card.get(card.get("id"), set())
            if any(re.search(_alias_pattern(alias), context, re.IGNORECASE) for alias in aliases):
                mentioned.append(card.get("id"))
        for index, left in enumerate(mentioned):
            for right in mentioned[index + 1:]:
                if left and right:
                    pairs.add(frozenset((left, right)))
    # A two-character production commonly states the separation once with
    # pronouns ("they remain physically separate") after introducing both
    # names.  The unambiguous pair can safely inherit that global constraint.
    ids = [card.get("id") for card in cards if card.get("id")]
    if not pairs and len(ids) == 2:
        pairs.add(frozenset(ids))
    return pairs


def _location_owner_ids(production, segment, aliases_by_card):
    """Find named owners of the clip's real location, excluding screen text."""
    setting = str(segment.get("setting", "") or "")
    owners = set()
    for card in production.get("cards", {}).get("characters", []):
        for alias in aliases_by_card.get(card.get("id"), set()):
            token = _alias_pattern(alias)
            patterns = (
                rf"{token}(?:'s|’s|的)\s*.{{0,60}}?(?:{_LOCATION_CUE})\b",
                rf"(?:{_LOCATION_CUE})\s+(?:of|belonging\s+to)\s+{token}\b",
                rf"{token}\s+(?:is|stands?|sits?|waits?)\s+(?:alone\s+)?(?:in|at)\s+(?:the\s+)?(?:{_LOCATION_CUE})\b",
            )
            if any(re.search(pattern, setting, re.IGNORECASE) for pattern in patterns):
                owners.add(card.get("id"))
                break
    return owners
_PHYSICAL_ACTIONS = (
    r"stands?|sits?|walks?|runs?|swims?|glides?|holds?|writes?|reads?|looks?|turns?|"
    r"smiles?|laughs?|cries?|opens?|closes?|picks?|places?|touches?|leans?|steps?|moves?|"
    r"enters?|exits?|records?|speaks?|says?|nods?|shakes?|raises?|lowers?|carries?|presses?|"
    r"taps?|watches?|gazes?|hesitates?|exhales?|faces?|reaches?|stops?|starts?|answers?|"
    r"站|坐|走|跑|游|拿|握|写|寫|读|讀|看|转|轉|笑|哭|打开|打開|关闭|關閉|捡|撿|放|"
    r"触|觸|靠|进入|進入|离开|離開|说|說|点头|點頭|摇头|搖頭|举|舉|按|凝视|凝視|"
    r"立つ|座る|歩く|走る|泳ぐ|持つ|書く|読む|見る|振り向く|笑う|泣く|開く|閉じる|"
    r"置く|触れる|入る|出る|話す|頷く|押す"
)


def _alias_pattern(alias):
    alias = str(alias or "").strip()
    if not alias:
        return r"(?!)"
    escaped = re.escape(alias)
    return (r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])"
            if re.search(r"[a-z0-9]", alias, re.IGNORECASE) else escaped)


def _alias_contexts(text, aliases):
    contexts = re.split(r"(?<=[.!?。！？;；])\s+|[\r\n]+", str(text or ""))
    return [context for context in contexts if any(
        re.search(_alias_pattern(alias), context, re.IGNORECASE) for alias in aliases)]


def _display_depiction(aliases, text, split_layout=False, declared_visible=False):
    if split_layout and declared_visible:
        return True
    for context in _alias_contexts(text, aliases):
        if not re.search(
                r"\b(?:phone\s+screen|screen|display|monitor|video|recording|photo|photograph|portrait|still)\b|"
                r"手机屏幕|手機螢幕|屏幕|螢幕|画面|畫面|视频|視訊|照片|肖像|スマートフォン画面|画面|動画|写真",
                context, re.IGNORECASE):
            continue
        for alias in aliases:
            token = _alias_pattern(alias)
            patterns = (
                rf"(?:face|image|portrait|photo|photograph|still|video)\s+(?:of\s+)?{token}"
                rf"(?!['’]s\s+(?:[A-Za-z][A-Za-z-]*\s+){{0,4}}(?:phone|smartphone|laptop|room|apartment|desk|card|letter|photo|photograph|video|recording)\b)",
                rf"{token}(?:'s|’s)?\s+(?:face|image|portrait|photo|photograph|still|video)\b",
                rf"{token}\s+(?:appears?|is\s+visible|smiles?|speaks?).{{0,60}}\b(?:on|inside|within|through)\s+(?:the\s+)?(?:phone\s+screen|screen|display|monitor|video)",
                rf"\b(?:phone\s+screen|screen|display|monitor)\b[^.;]{{0,80}}\b(?:showing|shows?|displays?)\s+(?:(?:a|the|live)\s+)*(?:(?:view|image|video|face|still)\s+)*(?:of\s+)?{token}(?!['’]s\s+(?:[A-Za-z][A-Za-z-]*\s+){{0,4}}(?:phone|smartphone|laptop|room|apartment|desk|card|letter|photo|photograph|video|recording)\b)",
                rf"\b(?:looks?|looking|watches?|watching|sees?|seeing)\s+(?:at\s+)?{token}(?!['’]s\s+(?:phone|smartphone|laptop|room|apartment|desk|card|letter|photo|photograph|video|recording))\s+(?:on|inside|within|through)\s+(?:the\s+)?(?:phone\s+screen|screen|display|monitor|video)",
                rf"\b(?:video|recording|message)\s+from\s+{token}",
            )
            if any(re.search(pattern, context, re.IGNORECASE) for pattern in patterns):
                return True
    return False


def _imagined_depiction(aliases, text):
    for context in _alias_contexts(text, aliases):
        for alias in aliases:
            token = _alias_pattern(alias)
            patterns = (
                rf"\b(?:image|vision|memory|thought|daydream)\s+of\s+{token}",
                rf"\b(?:imagines?|pictures?|remembers?|visuali[sz]es?)\b.{{0,100}}{token}",
                rf"{token}.{{0,100}}\b(?:appears?|materiali[sz]es?)\b.{{0,80}}\b(?:memory|thought|mind|daydream)\b",
                rf"(?:脑海|腦海|回忆|回憶|想象|想像|思い出|記憶|空想).{{0,80}}{token}",
            )
            if any(re.search(pattern, context, re.IGNORECASE) for pattern in patterns):
                return True
    return False


def _physically_staged(aliases, text):
    # Names written inside a card/message are story data, not another body.
    text = re.sub(r"(['\"“‘]).{0,240}?[\"'”’]", " ", str(text or ""))
    for context in _alias_contexts(text, aliases):
        # A sentence whose only visual relationship is a display or memory does
        # not physically stage that identity in the local room.
        if _display_depiction(aliases, context) or _imagined_depiction(aliases, context):
            continue
        for alias in aliases:
            token = _alias_pattern(alias)
            if re.search(
                    rf"(?:\b(?:close-up|medium shot|wide shot|camera|focus)\b.{{0,32}}\b(?:on|of|follows?)\b\s*)?"
                    rf"{token}\s+(?:is\s+|slowly\s+|quietly\s+|softly\s+|then\s+|immediately\s+)*"
                    rf"(?:{_PHYSICAL_ACTIONS})\b",
                    context, re.IGNORECASE):
                return True
            if re.search(rf"{token}\s+(?:is\s+)?(?:clearly\s+)?visible\b", context, re.IGNORECASE):
                return True
            if re.search(rf"\b(?:close-up|medium shot|wide shot|camera|focus)\b.{{0,40}}\b(?:on|of|follows?)\b\s*{token}",
                         context, re.IGNORECASE):
                return True
    return False


def character_presence_roles(production, segment):
    """Classify named identities by visual plane for one generated clip.

    Physical performers, people confined to a device/editorial panel, and
    clearly non-diegetic memories are different render contracts.  Treating all
    three as ``visible_subject_ids`` made H3 put a remote or remembered person
    beside the local actor and often duplicate that person again on the phone.
    """
    timeline = normalise_cast_timeline(
        segment.get("cast_timeline"), segment.get("card_selection", {}).get("characters", []))
    labelled = {key: {str(name).strip().casefold() for name in values}
                for key, values in timeline.items()}
    visible_labels = set().union(*(labelled[key] for key in
                                   ("visible_start", "visible_end", "enters", "exits")))
    selected_labels = {str(name).strip().casefold()
                       for name in segment.get("card_selection", {}).get("characters", [])}
    dialogue_labels = {str(line.get("speaker", "")).strip().casefold()
                       for line in segment.get("dialogue", []) if str(line.get("speaker", "")).strip()}
    voiceover_labels = {str(line.get("speaker", "")).strip().casefold()
                        for line in segment.get("dialogue", []) if line.get("voiceover")}
    text = "\n".join(str(segment.get(key, "")) for key in
                     ("title", "story", "setting", "action", "ending", "image_prompt"))
    staged_text = "\n".join(str(segment.get(key, "")) for key in
                            ("setting", "action", "ending", "image_prompt"))
    split_layout = _split_layout_requested(staged_text)
    roles = {}
    aliases_by_card = character_aliases(production, text)
    separated_pairs = remote_separated_pairs(production, aliases_by_card)
    location_owners = _location_owner_ids(production, segment, aliases_by_card)
    remote_device_context = bool(_DEVICE_CUE.search(staged_text) or split_layout)
    requested_view = str(segment.get("device_view", "auto") or "auto").strip().casefold()
    front_camera_view = requested_view == "front_camera" or (
        requested_view == "auto" and bool(_FRONT_CAMERA_CUE.search(staged_text)))
    for card in production.get("cards", {}).get("characters", []):
        aliases = aliases_by_card.get(card.get("id"), {str(card.get("name", "")).strip().casefold()})
        aliases = {alias for alias in aliases if alias}
        in_visible = any(alias in visible_labels for alias in aliases)
        in_offscreen = any(alias in labelled["offscreen"] for alias in aliases)
        in_mentioned = any(alias in labelled["mentioned_only"] for alias in aliases)
        selected = any(alias in selected_labels for alias in aliases)
        speaking = any(alias in dialogue_labels for alias in aliases)
        voiceover = any(alias in voiceover_labels for alias in aliases)
        display = _display_depiction(
            aliases, staged_text, split_layout,
            in_visible or in_offscreen or in_mentioned or selected)
        imagined = _imagined_depiction(aliases, staged_text)
        physical = _physically_staged(aliases, staged_text)
        voice_only = any(re.search(
            rf"(?:{_alias_pattern(alias)}(?:'s|’s)?\s+voice|voice\s+of\s+{_alias_pattern(alias)})",
            text, re.IGNORECASE) for alias in aliases)
        remote_from_local_owner = (
            remote_device_context and len(location_owners) == 1 and card.get("id") not in location_owners and
            any(frozenset((card.get("id"), owner)) in separated_pairs for owner in location_owners)
        )

        # Canonical geography outranks a planner sentence that accidentally
        # places a remote caller beside the local performer.  Front-camera POV
        # cannot simultaneously show the phone display, so a remote face in
        # that contradictory prose remains an off-screen participant.
        if remote_from_local_owner:
            if display and not front_camera_view:
                role = "display"
            elif speaking or voiceover or voice_only or in_visible or in_offscreen or selected:
                role = "offscreen"
            else:
                role = "absent"
        elif display and (split_layout or not physical):
            role = "display"
        elif imagined and not physical:
            role = "imagined"
        elif physical:
            role = "physical"
        elif in_offscreen or voiceover or voice_only:
            role = "offscreen"
        elif in_visible:
            role = "physical"
        elif in_mentioned:
            role = "absent"
        elif selected:
            role = "physical"
        elif speaking:
            role = "physical"
        else:
            role = "absent"
        roles[card["id"]] = role
    return roles


_DEVICE_CUE = re.compile(
    r"\b(?:phone|smartphone|mobile|laptop|notebook computer|computer|monitor|texting|text message|video call|video message)\b|"
    r"\b(?:sends?|sending|sent|opens?|opening|reads?|reading)\s+(?:a\s+|the\s+)?message\b|"
    r"(?<!off-)(?<!off )\b(?:screen|display)\b|"
    r"手机|手機|屏幕|螢幕|发信息|發信息|发消息|發消息|发信|發信|视频通话|視訊通話|"
    r"スマートフォン|携帯|画面|メッセージ|ビデオ通話",
    re.IGNORECASE,
)
_HANDHELD_DEVICE_CUE = re.compile(
    r"\b(?:phone|smartphone|mobile|cellphone|cell phone|tablet)\b|"
    r"手机|手機|移动电话|移動電話|平板|スマートフォン|携帯|タブレット",
    re.IGNORECASE,
)
_STATIC_DISPLAY_CUE = re.compile(
    r"\b(?:laptop|notebook computer|desktop computer|computer monitor|monitor|desktop display)\b|"
    r"笔记本电脑|筆記本電腦|电脑屏幕|電腦螢幕|显示器|顯示器|"
    r"ノートパソコン|パソコン|コンピューターモニター|モニター",
    re.IGNORECASE,
)
_PHONE_TO_EAR_CUE = re.compile(
    r"\b(?:phone|smartphone|mobile)\s+(?:to|against|near)\s+(?:his|her|their|the)?\s*ear\b|"
    r"\b(?:holds?|raises?|brings?)\s+(?:the|a|his|her|their)?\s*(?:phone|smartphone|mobile)\s+(?:to|against)\s+(?:his|her|their|the)?\s*ear\b|"
    r"手机.{0,12}(?:耳边|耳旁)|手機.{0,12}(?:耳邊|耳旁)|耳.{0,8}(?:手机|手機)|"
    r"(?:スマートフォン|携帯).{0,12}(?:耳|耳元)",
    re.IGNORECASE,
)
_SCREEN_VIEW_CUE = re.compile(
    r"\b(?:insert shot|screen insert|over[- ]the[- ]shoulder|over (?:his|her|their) shoulder)\b|"
    r"\b(?:screen|display)\b.{0,80}\b(?:shows?|showing|displays?|displaying|reveals?|transitions?|interface|still|image|face|video)\b|"
    r"\b(?:shows?|showing|displays?|displaying)\b.{0,80}\b(?:on|inside|within)\s+(?:the\s+)?(?:phone\s+)?(?:screen|display)\b|"
    r"\b(?:screen|display)\s+(?:in|fills?)\s+(?:the\s+)?foreground\b|"
    r"\b(?:focus|rack focus)\b.{0,80}\b(?:screen|display)\b|"
    r"(?:插入镜头|插入鏡頭|过肩|過肩|肩越し).{0,80}(?:屏幕|螢幕|画面)|"
    r"(?:屏幕|螢幕|画面).{0,80}(?:显示|顯示|映出|映る|表示|切换|切換)",
    re.IGNORECASE,
)
_FRONT_CAMERA_CUE = re.compile(
    r"\b(?:looks?|looking|speaks?|speaking|talks?|talking)\s+(?:directly\s+)?(?:at|into|to)\s+(?:the\s+)?(?:phone\s+)?(?:front[- ]facing\s+)?camera\b|"
    r"\b(?:front[- ]facing camera|selfie camera|phone camera point of view|phone-camera pov)\b|"
    r"(?:看向|对着|對著).{0,16}(?:手机镜头|手機鏡頭|前置镜头|前置鏡頭)|"
    r"(?:スマートフォン|携帯).{0,12}(?:カメラ|レンズ).{0,16}(?:見る|話す)",
    re.IGNORECASE,
)
_MIRROR_CUE = re.compile(
    r"\b(?:mirror|reflection|reflected|looking glass)\b|"
    r"镜子|鏡子|镜中|鏡中|倒影|反射|ミラー|鏡|反射",
    re.IGNORECASE,
)
_INSCRIBED_PROP_CUE = re.compile(
    r"\b(?:card|letter|note|envelope|label|sign|document|paper|ticket|postcard|photograph|photo)\b|"
    r"卡片|贺卡|賀卡|信件|信封|纸条|紙條|便签|便箋|标签|標籤|标牌|標牌|文件|照片|"
    r"カード|手紙|封筒|メモ|ラベル|標識|書類|写真",
    re.IGNORECASE,
)


def device_screen_geometry_lock(segment, display_names=()):
    """Choose one physically possible phone/display composition for a clip.

    A conventional external camera cannot see a holder's unobstructed frontal
    face and a square-on phone screen at the same time: both surfaces face one
    another.  Leaving that geometry implicit made generated phones transparent,
    double-sided or detached from the hands.  This lock gives the renderer one
    view to solve and keeps any display content rigidly inside the device.
    """
    text = "\n".join(str(segment.get(key, "")) for key in
                     ("story", "setting", "action", "ending", "image_prompt"))
    contract = "DEVICE GEOMETRY CONTRACT V2. "
    requested = str(segment.get("device_view", "auto") or "auto").strip().casefold()
    if requested not in DEVICE_VIEW_MODES:
        requested = "auto"
    if requested == "auto" and not _DEVICE_CUE.search(text):
        return ""
    mode = requested
    split_layout = _split_layout_requested(text)
    static_only = bool(_STATIC_DISPLAY_CUE.search(text) and not _HANDHELD_DEVICE_CUE.search(text))
    forced_remote_panel = bool(split_layout and len(tuple(display_names)) >= 2)
    corrected_phone_to_ear = bool(_PHONE_TO_EAR_CUE.search(text) and requested == "performance")
    if forced_remote_panel:
        # A planner may save ``performance`` while its own prose explicitly
        # describes two remote rooms.  Geography is a hard physical fact and
        # therefore outranks a generic view preference.
        mode = "remote_panel"
    elif corrected_phone_to_ear:
        # A phone pressed to the ear has only one physically valid display
        # orientation.  Planner-selected "performance" is descriptive, not a
        # licence to put video or chat pixels on the outward phone back.
        mode = "phone_to_ear"
    elif mode == "auto":
        if split_layout and display_names:
            mode = "remote_panel"
        elif _PHONE_TO_EAR_CUE.search(text):
            mode = "phone_to_ear"
        # An authored over-shoulder/insert always wins over a generic phrase
        # such as "speaks to the camera" later in the same description.
        elif _SCREEN_VIEW_CUE.search(text) or display_names:
            mode = "screen"
        elif _FRONT_CAMERA_CUE.search(text):
            mode = "front_camera"
        else:
            mode = "performance"
    override = (
        " PHYSICAL GEOGRAPHY OVERRIDE: the explicitly separate remote locations outrank the conflicting saved view label."
        if forced_remote_panel and requested != "auto" else
        " PHYSICAL GEOMETRY OVERRIDE: the authored phone-to-ear action outranks the conflicting performance-view label."
        if corrected_phone_to_ear else
        (" DIRECTOR OVERRIDE: this manually selected view outranks conflicting automatic framing language."
         if requested != "auto" else ""))
    if mode == "remote_panel":
        return (
            contract + "REMOTE-CALL PANEL GEOMETRY LOCK: use one clean editorial split with exactly one bounded panel for each remote location and one instance of each participant. "
            "Each panel has its own consistent background, light, camera axis and eyeline; the participants look toward the shared panel boundary but never occupy the same room, overlap panels or appear again inside a phone. "
            "Do not alternate compositions, add extra tiles, nest a screen inside a screen, mirror either participant or duplicate either identity." + override)
    if static_only:
        remote = (" The only identity permitted inside the static display is " +
                  ", ".join(display_names) + "." if display_names else "")
        return (
            contract + "STATIC-DISPLAY GEOMETRY LOCK: use exactly one opaque laptop or monitor as a fixed physical prop with one bounded front display. "
            "All interface, photograph, video and remote-person pixels remain clipped inside its bezel and share the display perspective. "
            "Do not invent a phone, a hand-held foreground device, a foreground viewer, a second computer, a recursive screen or a full-size physical copy of anyone shown on the display. "
            "If a local performer is also visible, choose one coherent camera side: either a screen-focused insert with only hands/shoulder/partial profile, or a performance view with the display oblique or background-readable; never show the same local identity as both a frontal foreground body and another frontal background body."
            + remote + override)
    common = (
        "Use exactly one rigid opaque phone/device with one front screen and one back. "
        "Its four corners, bezel, reflections and displayed pixels share the same perspective and follow the hand as one object. "
        "Clip every image and interface element inside the bezel; no transparent, mirrored, floating, detached, rear-facing or double-sided screen, no second phone, and no fingers passing through the device."
    )
    if mode == "phone_to_ear":
        return (
            contract + "PHONE-TO-EAR GEOMETRY LOCK: the speaker grille is held naturally at the ear; the screen faces inward/away from the audience and is not visible or readable. "
            "Do not place a remote face, chat interface or glowing image on the outward phone back. " + common + override)
    if mode == "screen":
        remote = (" The only person permitted inside the display is " + ", ".join(display_names) + "."
                  if display_names else "")
        return (
            contract + "SCREEN-VIEW GEOMETRY LOCK: use a plausible over-the-shoulder, side-over-shoulder or insert composition from the holder's side of the screen axis. "
            "The audience may see the display face, while the holder is limited to naturally gripping hands, shoulder, back of head or a partial three-quarter profile; do not also show the holder's unobstructed frontal face. "
            "Those partial foreground body parts and any frontal background performer must never be two copies of the same identity: choose one representation of the holder, never both. "
            "Keep the phone at a natural viewing distance and orientation, portrait unless the authored action explicitly requires landscape. "
            "The holder's eyes aim at the physical screen and any tap lands on that same screen plane." + remote + " " + common + override)
    if mode == "front_camera":
        return (
            contract + "FRONT-CAMERA GEOMETRY LOCK: use the phone camera's point of view; the performer looks into the lens beside the screen. "
            "The phone body and its screen are outside this camera view, so do not superimpose an interface, a second view of the performer or a floating phone. " + common + override)
    return (
        contract + "PERFORMANCE-VIEW GEOMETRY LOCK: prioritize the holder's face, eyeline and hand performance. "
        "The screen faces the holder and away from the audience, so show only the opaque phone back or thin edge; its content is not visible or readable to the audience. "
        "Do not rotate the display toward the audience while the holder remains front-facing. " + common + override)


def inscribed_prop_continuity_lock(cards):
    """Keep authored cards, letters and similar written props visually stable.

    Generative video cannot reliably typeset long exact copy.  The safest
    production contract is therefore to preserve one physical prop and one
    inscription layout, use only authored wording, and reserve a readable
    hero insert for post-composited typography.
    """
    selected = []
    for card in cards or ():
        text = "\n".join(str(card.get(key, "")) for key in ("name", "description", "notes"))
        if _INSCRIBED_PROP_CUE.search(text):
            selected.append(str(card.get("name", "")).strip())
    selected = list(dict.fromkeys(name for name in selected if name))
    if not selected:
        return ""
    return (
        "INSCRIBED-PROP CONTINUITY LOCK: " + ", ".join(selected) +
        " must remain the same continuing physical prop across the clip. Preserve its size, material, colour, folds, wear, orientation and inscription layout; never replace it with a newly designed copy. "
        "Use only wording explicitly authored in the selected prop card or current screenplay and never invent, paraphrase, translate, extend or mutate its text between shots. "
        "If exact words must be readable, use one stable front-facing insert with typography composited in post; otherwise keep the writing naturally too small or oblique to read. "
        "Do not create floating text, subtitles, duplicate cards, extra notes or a second readable face of the prop.")


def effective_cast_timeline(production, segment, roles=None):
    """Return the physical/off-screen timeline after visual-plane repair."""
    roles = roles or character_presence_roles(production, segment)
    cards = production.get("cards", {}).get("characters", [])
    aliases_by_card = character_aliases(production)
    alias_role = {alias: roles.get(card.get("id"), "absent")
                  for card in cards for alias in aliases_by_card.get(card.get("id"), set())}

    def role_for(name):
        return alias_role.get(str(name or "").strip().casefold(), "absent")

    source = normalise_cast_timeline(
        segment.get("cast_timeline"), segment.get("card_selection", {}).get("characters", []))
    physical = {key: [name for name in source[key] if role_for(name) == "physical"]
                for key in ("visible_start", "visible_end", "enters", "exits")}
    physical["offscreen"] = list(dict.fromkeys(
        [name for name in source["offscreen"] if role_for(name) == "offscreen"] +
        [card["name"] for card in cards if roles.get(card.get("id")) == "offscreen"]))
    physical["mentioned_only"] = list(dict.fromkeys(
        [name for name in source["mentioned_only"] if role_for(name) == "absent"] +
        [card["name"] for card in cards if roles.get(card.get("id")) == "absent" and
         any(alias in {str(value).strip().casefold() for value in source["mentioned_only"]}
             for alias in aliases_by_card.get(card.get("id"), set()))]))
    return physical


def effective_temporal_cast_lock(production, segment, roles=None):
    """Render the saved timeline after separating physical and visual planes."""
    roles = roles or character_presence_roles(production, segment)
    cards = production.get("cards", {}).get("characters", [])
    physical = effective_cast_timeline(production, segment, roles)
    effective = copy.deepcopy(segment)
    effective["cast_timeline"] = physical
    display = [card["name"] for card in cards if roles.get(card.get("id")) == "display"]
    imagined = [card["name"] for card in cards if roles.get(card.get("id")) == "imagined"]
    text = "\n".join(str(segment.get(key, "")) for key in
                     ("story", "setting", "action", "ending", "image_prompt"))
    extra = [
        "Visible only inside a bounded device/remote display: " + (", ".join(display) or "none") + ".",
        "Visible only as a non-diegetic thought/memory image: " + (", ".join(imagined) or "none") + ".",
        "A display or memory identity is not a physical person in the local setting.",
    ]
    if (display or _split_layout_requested(text) or _DEVICE_CUE.search(text) or
            str(segment.get("device_view", "auto") or "auto").strip().casefold() != "auto"):
        extra.append(
            "DEVICE INTERACTION LOCK: use exactly one local phone/device prop. Its screen content stays geometrically inside the bezel and never becomes a second real room or a second full-size body. For typing or sending, show a readable hand-to-device action and one deliberate tap with a simple non-verbal sent-state cue; do not invent extra chat messages, subtitles, captions, floating UI, extra hands or a second phone. Exact on-screen typography should be added in post rather than hallucinated by the video model.")
        extra.append(device_screen_geometry_lock(segment, display))
    if _MIRROR_CUE.search(text):
        extra.append(
            "MIRROR GEOMETRY LOCK: each physically present identity may have at most one optically plausible reflection of that same identity, confined to the mirror surface and matching the real body's wardrobe, pose and action. A reflection is not another physical body. Display-only, imagined, off-screen and absent identities never appear in the mirror or elsewhere in the room. Do not add extra mirror panels, portraits, reflected phones or duplicate figures.")
    absent = [card["name"] for card in cards if roles.get(card.get("id")) == "absent"]
    if absent:
        extra.append(
            "EMOTIONAL SUBTEXT LOCK: express longing, affection or thought about absent identities only through the physical actor's eyes, breath, pause, posture and an already scripted prop or screen cue. Do not materialize an absent person as a companion, ghost, silhouette, reflection, portrait or background extra unless that identity is explicitly listed in the display or memory roster above.")
    return temporal_cast_lock(effective) + "\n" + "\n".join(extra)


def production_render_override(production, segment, relevant_cards=None, roles=None):
    """Build the immutable guard appended after every generated H3 prompt.

    Production context guides the local prompt model, but editable AI prose can
    still paraphrase away a cast, mirror, device or written-prop constraint.
    This final block is compiled after that prose and therefore remains the
    render authority for both classic and narrative prompt versions.
    """
    roles = roles or character_presence_roles(production, segment)
    if relevant_cards is None:
        selected = {str(name).strip().casefold()
                    for name in segment.get("card_selection", {}).get("props", [])}
        props = [card for card in production.get("cards", {}).get("props", [])
                 if card.get("name", "").strip().casefold() in selected]
    else:
        props = relevant_cards.get("props", [])
    guard = effective_temporal_cast_lock(production, segment, roles)
    prop_guard = inscribed_prop_continuity_lock(props)
    state = normalise_continuity_state(segment.get("continuity_state"), segment)
    positions_start = "; ".join(
        f"{row['character']}={row['position']} facing {row['facing'] or 'unchanged'}, moving {row.get('movement_direction') or 'unchanged'}, eyeline {row.get('eyeline_direction') or 'unchanged'} toward {row.get('eyeline_target') or 'authored target'}"
        for row in state["positions_start"]) or "not additionally specified"
    positions_end = "; ".join(
        f"{row['character']}={row['position']} facing {row['facing'] or 'unchanged'}, moving {row.get('movement_direction') or 'unchanged'}, eyeline {row.get('eyeline_direction') or 'unchanged'} toward {row.get('eyeline_target') or 'authored target'}"
        for row in state["positions_end"]) or "not additionally specified"
    holders_start = "; ".join(f"{row['prop']} held by {row['holder']}"
                              + (f" ({row['state']})" if row.get("state") else "")
                              for row in state["prop_holders_start"]) or "not additionally specified"
    holders_end = "; ".join(f"{row['prop']} held by {row['holder']}"
                            + (f" ({row['state']})" if row.get("state") else "")
                            for row in state["prop_holders_end"]) or "not additionally specified"
    manifest = production.get("source_manifest") or production_source_manifest(production)
    refs = segment.get("source_refs", {})
    source_rows = {row["id"]: row for kind in ("scenes", "paragraphs", "dialogue", "events")
                   for row in _flatten_manifest(manifest, kind)}
    source_parts = []
    for label, key in (("Original scene", "scene_ids"), ("Original paragraph", "paragraph_ids"),
                       ("Original dialogue", "dialogue_ids"), ("Plot event", "event_ids")):
        values = []
        for ident in refs.get(key, []):
            row = source_rows.get(ident, {})
            value = row.get("title") or ((row.get("speaker", "") + ": " if row.get("speaker") else "") +
                                          str(row.get("text", "")))
            if value:
                values.append(f"{ident}={value}")
        if values:
            source_parts.append(label + ": " + " | ".join(values))
    source_guard = (
        "SOURCE COVERAGE LOCK: this clip is responsible only for the following authored material, in this order. " +
        "\n".join(source_parts))[:7000]
    continuity_guard = (
        "ADJACENT CLIP STATE LOCK: opening=" + (state["opening_state"] or "use the authored opening") +
        "; ending=" + (state["ending_state"] or "use the authored ending") +
        ". Opening positions: " + positions_start + ". Ending positions: " + positions_end +
        ". Opening prop holders: " + holders_start + ". Ending prop holders: " + holders_end +
        f". Cut relation={segment.get('transition_mode', 'hard_cut')}; MMH3 continuation allowed=" +
        ("yes" if segment.get("mmh3_allowed") else "no") +
        ". Do not repeat the preceding action, swap a prop holder, reverse established screen direction, "
        "or import an off-screen/display/memory identity into the physical set.")
    shot_contract = normalise_shot_contract(segment.get("shot_contract"), segment)
    previous = next((row for row in production.get("segments", [])
                     if row.get("index") == segment.get("index", 0) - 1), None)
    previous_snapshot = "none; this is the opening clip"
    if previous:
        prior_state = normalise_continuity_state(previous.get("continuity_state"), previous)
        prior_shot = normalise_shot_contract(previous.get("shot_contract"), previous)
        previous_snapshot = (
            f"ending state={prior_state['ending_state']}; ending composition={prior_shot['ending_composition']}; "
            f"shot size={prior_shot['shot_size']}; camera axis={prior_shot['camera_axis'] or 'unspecified'}; "
            "positions=" + ("; ".join(
                f"{row['character']} {row['position']} facing {row['facing']}, moving {row.get('movement_direction')}, eyeline {row.get('eyeline_direction')} toward {row.get('eyeline_target')}"
                for row in prior_state["positions_end"]) or "unspecified") + "; props=" +
            ("; ".join(f"{row['prop']} held by {row['holder']}"
                       + (f" ({row['state']})" if row.get("state") else "")
                       for row in prior_state["prop_holders_end"])
             or "unspecified"))
    edit_guard = (
        f"SHOT EDIT CONTRACT: role={shot_contract['role']}; shot size={shot_contract['shot_size']}; "
        f"opening composition={shot_contract['opening_composition']}; ending composition={shot_contract['ending_composition']}; "
        f"axis={shot_contract['camera_axis'] or 'unspecified'}; allow axis crossing={shot_contract['allow_axis_cross']}; "
        f"edit reason={shot_contract['edit_reason']}; relation to previous={shot_contract['relation_previous']}. "
        f"PREVIOUS END SNAPSHOT: {previous_snapshot}. Preserve: {shot_contract['preserve_from_previous'] or 'only authored continuity'}. "
        f"Must change: {shot_contract['must_change'] or 'only what the authored beat changes'}. "
        "Keep reciprocal dialogue eyelines, screen direction and action phase. Avoid an accidental jump cut or replaying the preceding action.")
    return "\n\n".join(part for part in [
        "FINAL PRODUCTION RENDER OVERRIDE — this block supersedes any conflicting earlier staging, cast, screen, reflection, prop or audio prose. Do not reinterpret it as story content.",
        guard,
        source_guard,
        continuity_guard,
        edit_guard,
        prop_guard,
    ] if part)


def segment_identity_repair_reasons(production, segment):
    """Diagnose only saved clips whose old identity contract is unsafe.

    A renderer-version bump invalidates every clip in every project.  Identity
    repairs are narrower: voice-card labels used as people, visible speakers
    omitted from the cast timeline, and already-rendered text-only characters
    without any visual authority.  Return per-clip reasons so unaffected takes
    remain ready and selectable.
    """
    characters = {card.get("id"): card for card in production.get("cards", {}).get("characters", [])}
    alias_sets = character_aliases(production)
    aliases = {
        alias: characters[card_id]
        for card_id, values in alias_sets.items()
        if card_id in characters
        for alias in values
    }
    voice_aliases = voice_character_aliases(production)
    reasons = []
    legacy_voice_labels = []
    for line in segment.get("dialogue", []):
        label = str(line.get("speaker", "")).strip()
        key = label.casefold()
        voice_owner = voice_aliases.get(key)
        character_owner = aliases.get(key)
        # A voice card is commonly named after its owning character (for
        # example both the character card and voice card are named ``Mimi``).
        # That overlap is a valid canonical screenplay speaker, not a legacy
        # voice-ID leak.  Only diagnose labels which resolve through the voice
        # library without resolving to the same character through the normal
        # character alias table (for example ``MIMI_V1``).
        if (voice_owner is not None and
                (character_owner is None or
                 character_owner.get("id") != voice_owner.get("id"))):
            legacy_voice_labels.append(label)
    legacy_voice_labels = sorted(dict.fromkeys(legacy_voice_labels))
    if legacy_voice_labels:
        reasons.append(
            "本段对白把声线卡 ID 当成了画面角色（" + ", ".join(legacy_voice_labels) +
            "），请只重新生成本段提示词和视频")

    roles = character_presence_roles(production, segment)
    timeline_labels = {
        str(name).strip().casefold()
        for key in ("visible_start", "visible_end", "enters", "exits")
        for name in segment.get("cast_timeline", {}).get(key, [])
        if str(name).strip()
    }
    misplaced_remote = [
        card.get("name", "") for card in characters.values()
        if roles.get(card.get("id")) in ("display", "offscreen", "imagined") and
        any(alias in timeline_labels for alias in alias_sets.get(card.get("id"), set()))
    ]
    if misplaced_remote:
        reasons.append(
            "本段把远程屏幕、电话声音或回忆人物误列为现场角色（" +
            ", ".join(misplaced_remote) + "），请重新生成本段提示词和视频")

    render_text = "\n".join(str(segment.get(key, "")) for key in
                              ("story", "setting", "action", "ending", "image_prompt"))
    display_names = [card.get("name", "") for card_id, card in characters.items()
                     if roles.get(card_id) == "display"]
    current_device_lock = device_screen_geometry_lock(segment, display_names)
    saved_prompt = str(segment.get("video_prompt", "") or "")
    device_upgrade_needed = bool(
        ("STATIC-DISPLAY GEOMETRY LOCK" in current_device_lock and
         "STATIC-DISPLAY GEOMETRY LOCK" not in saved_prompt) or
        ("REMOTE-CALL PANEL GEOMETRY LOCK" in current_device_lock and
         "REMOTE-CALL PANEL GEOMETRY LOCK" not in saved_prompt) or
        ("SCREEN-VIEW GEOMETRY LOCK" in current_device_lock and
         "DEVICE GEOMETRY CONTRACT V2" not in saved_prompt))
    if saved_prompt and device_upgrade_needed:
        reasons.append(
            "本段使用手机、电脑屏幕或远程画面，但保存的提示词仍是旧版设备构图规则；"
            "只需重新生成本段提示词和视频，不需要重新分镜")
    if (segment.get("project_id") and
            segment.get("reference_strategy_version", 0) < REFERENCE_STRATEGY_VERSION and
            (_DEVICE_CUE.search(render_text) or _MIRROR_CUE.search(render_text) or
             len(timeline_labels) > 1 or _INSCRIBED_PROP_CUE.search(render_text))):
        reasons.append("本段需要新的最终人物、屏幕、镜子与文字道具渲染保护，请重新生成提示词后再生成视频")

    timeline = segment.get("cast_timeline", {})
    visible_timeline = {
        str(name).strip().casefold()
        for key in ("visible_start", "enters", "exits", "visible_end")
        for name in timeline.get(key, [])
        if str(name).strip()
    }
    missing_visible_speakers = []
    for line in segment.get("dialogue", []):
        if line.get("voiceover"):
            continue
        raw = re.split(r"[|｜]", str(line.get("speaker", "")).strip().casefold(), maxsplit=1)[0].strip()
        character = aliases.get(raw) or voice_aliases.get(raw)
        canonical = str(character.get("name", "")).strip() if character else ""
        # A remote/display speaker can be audible without occupying the local
        # physical cast timeline.  The visual-plane role is authoritative here;
        # only a genuinely physical speaker must be added to visible_start/end.
        if character and roles.get(character.get("id")) != "physical":
            continue
        if canonical and canonical.casefold() not in visible_timeline:
            missing_visible_speakers.append(canonical)
    if missing_visible_speakers:
        reasons.append(
            "本段可见说话角色未进入角色时间线（" + ", ".join(dict.fromkeys(missing_visible_speakers)) +
            "），请只重新规划并重做本段")

    has_render = bool(segment.get("selected_video_run_id") or segment.get("last_video_run_id"))
    has_character_overview = bool(production.get("overview_asset_ids", {}).get("characters"))
    if has_render and not has_character_overview:
        selected = {
            str(name).strip().casefold()
            for name in segment.get("card_selection", {}).get("characters", [])
            if str(name).strip()
        }
        missing_images = [
            card.get("name", "") for card in characters.values()
            if not card.get("asset_ids") and any(alias in selected for alias in
                                                  alias_sets.get(card.get("id"), set()))
        ]
        if missing_images:
            reasons.append(
                "本段已生成视频中的可见角色缺少独立参考图或角色总图（" +
                ", ".join(missing_images) + "）；补图后只重做本段")
    return reasons


def repair_segment_identity_contract(production, segment):
    """Repair legacy speaker identity locally while rebuilding one clip."""
    characters = {card.get("id"): card for card in production.get("cards", {}).get("characters", [])}
    aliases = {
        alias: characters[card_id]
        for card_id, values in character_aliases(production).items()
        if card_id in characters
        for alias in values
    }
    aliases.update(voice_character_aliases(production))
    timeline = segment.setdefault("cast_timeline", normalise_cast_timeline(None))
    changes = []
    for line in segment.get("dialogue", []):
        raw = re.split(r"[|｜]", str(line.get("speaker", "")).strip().casefold(), maxsplit=1)[0].strip()
        character = aliases.get(raw)
        if character is None:
            continue
        canonical = str(character.get("name", "")).strip()
        if not canonical:
            continue
        if line.get("speaker") != canonical:
            changes.append(f"bound speaker {line.get('speaker')} to {canonical}")
            line["speaker"] = canonical
        if line.get("voiceover"):
            if canonical not in timeline["offscreen"]:
                timeline["offscreen"].append(canonical)
            continue
        visible = {
            str(name).strip().casefold()
            for key in ("visible_start", "enters", "exits", "visible_end")
            for name in timeline.get(key, [])
        }
        if canonical.casefold() not in visible:
            timeline["visible_start"].append(canonical)
            timeline["visible_end"].append(canonical)
            changes.append(f"restored visible speaker {canonical} to this clip")
        for key in ("offscreen", "mentioned_only"):
            timeline[key] = [name for name in timeline[key] if str(name).strip().casefold() != canonical.casefold()]
        selected = segment.setdefault("card_selection", {}).setdefault("characters", [])
        if canonical not in selected:
            selected.append(canonical)
    if changes:
        warning = "Single-clip identity repair: " + "; ".join(changes)
        segment["continuity_warnings"] = list(dict.fromkeys(
            [*segment.get("continuity_warnings", []), warning]))[:16]
    return changes


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


def render_visual_style(value):
    """Keep the artwork treatment while removing model-sheet composition cues.

    Character cards commonly describe their source image as a turnaround or
    expression sheet.  Those words are useful when cataloguing the card, but
    putting them into a video prompt asks the renderer to reproduce several
    views of the same identity.  The saved card/style text remains untouched;
    only the clip-time rendering direction is cleaned here.
    """
    text = " ".join(str(value or "").split())
    replacements = (
        (r"(?i)\bcharacter\s+(?:model\s+|reference\s+)?sheet\b", ""),
        (r"(?i)\bmodel\s+sheet\b", ""),
        (r"(?i)\breference\s+sheet\b", ""),
        (r"(?i)\bexpression\s+sheet\b", ""),
        (r"(?i)\bpose\s+sheet\b", ""),
        (r"(?i)\bcontact\s+sheet\b", ""),
        (r"(?i)\bturn[ -]?around(?:s)?\b", ""),
        (r"(?i)\bfront\s*[,/+-]?\s*side\s*[,/+-]?\s*(?:and\s+)?back(?:\s+views?)?\b", ""),
        (r"(?i)\bmultiple\s+views?\b", ""),
        (r"(?i)\borthographic\s+views?\b", ""),
        (r"(?i)\b(?:clean|plain|pure)?\s*white\s+background\b", ""),
        (r"角色(?:设定|設定|参考|參考)图", ""),
        (r"角色(?:设定|設定|参考|參考)表", ""),
        (r"三[视視]图", ""),
        (r"正[侧側]背(?:面)?", ""),
        (r"正面[、,，/ ]*[侧側]面[、,，/ ]*背面", ""),
        (r"表情(?:设定|設定)?(?:图|圖|表)", ""),
        (r"(?:纯|純|干净|乾淨)?白色背景", ""),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+([,.;:，。；：])", r"\1", text)
    text = re.sub(r"([,;，；])\s*(?:[,;，；]\s*)+", r"\1 ", text)
    text = re.sub(r"(?:^|\s)[,;，；]+\s*", " ", text)
    return " ".join(text.split()).strip(" ,;，；")


def _primary_character_view(source):
    """Crop a model sheet to its leftmost full-height identity view.

    The operation is deliberately conservative: it only crops landscape
    images with a near-uniform border and several disconnected foreground
    regions.  Portraits, illustrations and busy-background references pass
    through unchanged.
    """
    from PIL import Image, ImageChops

    image = source.convert("RGB")
    width, height = image.size
    if width < height * 1.18 or min(width, height) < 96:
        return image

    sample_points = (
        (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1),
        (width // 2, 0), (width // 2, height - 1),
        (0, height // 2), (width - 1, height // 2),
    )
    samples = [image.getpixel(point) for point in sample_points]
    background = tuple(sorted(pixel[channel] for pixel in samples)[len(samples) // 2]
                       for channel in range(3))
    if max(max(pixel[channel] for pixel in samples) - min(pixel[channel] for pixel in samples)
           for channel in range(3)) > 54:
        return image

    scale = min(1.0, 480 / width)
    reduced = image.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                           Image.Resampling.LANCZOS)
    flat = Image.new("RGB", reduced.size, background)
    mask = ImageChops.difference(reduced, flat).convert("L").point(lambda value: 255 if value > 22 else 0)
    mask_width, mask_height = mask.size
    pixels = mask.load()
    visited = bytearray(mask_width * mask_height)
    components = []
    for y in range(mask_height):
        for x in range(mask_width):
            offset = y * mask_width + x
            if visited[offset] or not pixels[x, y]:
                continue
            stack = [(x, y)]
            visited[offset] = 1
            left = right = x
            top = bottom = y
            area = 0
            while stack:
                current_x, current_y = stack.pop()
                area += 1
                left, right = min(left, current_x), max(right, current_x)
                top, bottom = min(top, current_y), max(bottom, current_y)
                for next_y in range(max(0, current_y - 1), min(mask_height, current_y + 2)):
                    for next_x in range(max(0, current_x - 1), min(mask_width, current_x + 2)):
                        next_offset = next_y * mask_width + next_x
                        if not visited[next_offset] and pixels[next_x, next_y]:
                            visited[next_offset] = 1
                            stack.append((next_x, next_y))
            if area >= mask_width * mask_height * .0015:
                components.append((left, top, right + 1, bottom + 1, area))

    tall = [component for component in components
            if component[3] - component[1] >= mask_height * .43
            and component[2] - component[0] <= mask_width * .58]
    meaningful = [component for component in components
                  if component[4] >= mask_width * mask_height * .003]
    if not tall or len(meaningful) < 2:
        return image
    selected = min(tall, key=lambda component: (component[0], -component[4]))

    inverse = 1 / scale
    left, top, right, bottom = [round(value * inverse) for value in selected[:4]]
    # Turnaround sheets normally place the canonical front view in the first
    # panel.  Even when floor shadows or touching limbs merge several views
    # into one mask component, never let the automatic identity tile cross
    # into the adjacent panel.
    sheet_panel_right = round(width * .27)
    if left <= width * .12 and sheet_panel_right > left + width * .10:
        right = min(right, sheet_panel_right)
    pad_x = max(12, round((right - left) * .18))
    pad_y = max(10, round((bottom - top) * .08))
    crop = (max(0, left - pad_x), max(0, top - pad_y),
            min(sheet_panel_right if left <= width * .12 else width, right + pad_x),
            min(height, bottom + pad_y))
    if crop[2] - crop[0] < width * .12 or crop[3] - crop[1] < height * .45:
        return image
    return image.crop(crop)


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
    direction = render_visual_style(direction)
    return ("Single visual-style authority for this clip and every camera beat: " + direction
            + " Keep the same rendering medium, character design, palette, lighting logic and texture. "
              "A style reference transfers treatment only, never its depicted people, props or location. "
              "Uploaded identity references provide appearance evidence only: render exactly one spatial "
              "instance of each named subject, never extra copies, inset portraits, lineup panels, printed "
              "labels or a catalog background.")


def raw_estimate_seconds(text):
    han = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    punctuation = len(re.findall(r"[，。！？；,.!?;:]", text))
    speech = han / 4.2 + words / 2.45 + min(2.5, punctuation * .16)
    action = 1.7 + max(1, punctuation) * .75 + len(text) / 100
    return max(action, speech + 1.8)


def estimate_seconds(text):
    return max(PLANNED_MIN_SECONDS, min(MAX_SECONDS, math.ceil(raw_estimate_seconds(text))))


def dialogue_minimum_seconds(segment):
    """Return a conservative clip floor for exact authored speech.

    Dialogue is immutable source material in production projects.  Giving a
    35-40 word exchange a nominal ten seconds forces H3 to rush, truncate or
    invent timing.  Allocate enough of the existing episode budget before
    distributing spare action time; explicit source timecodes still win.
    """
    text = " ".join(str(line.get("text", "")).strip()
                    for line in segment.get("dialogue", []) if str(line.get("text", "")).strip())
    return estimate_seconds(text) if text else PLANNED_MIN_SECONDS


def fit_planned_durations(segments, target_seconds, language="en", source_story=None):
    """Preserve the plan while making its generated duration match the episode target."""
    result = copy.deepcopy(segments)
    count = len(result)
    target = int(round(target_seconds))
    requested_target = target
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
    if timed_groups and len(timed_groups) == count:
        preferred = [group["duration"] for group in timed_groups]
        durations = [min(MAX_SECONDS, max(PLANNED_MIN_SECONDS, value)) for value in preferred]
    elif timed_groups:
        # A timed parent block may be split into several generation clips, but
        # its authored start/end remains exact.  Dialogue estimates help an
        # untimed plan grow naturally; they must not silently expand a 10s
        # authored block to 20s and break the parent/child mapping.
        preferred = [max(before, estimate_seconds(
            " ".join(x for x in (str(item.get("action", "")).strip(),
                                  " ".join(str(line.get("text", "")).strip()
                                           for line in item.get("dialogue", []))) if x)))
                     for item, before in zip(result, original)]
        durations = [PLANNED_MIN_SECONDS] * count
    else:
        speech_floors = [dialogue_minimum_seconds(item) for item in result]
        # Episode length is an editorial target, while authored dialogue is an
        # exact source constraint.  A modest overrun must not discard an
        # otherwise valid AI storyboard and replace it with blind text chunks.
        # Keep every clip within 5-15 seconds and extend only the generated
        # total to the minimum natural speech budget.
        target = max(target, sum(speech_floors))
        preferred = [max(before, floor, estimate_seconds(
            " ".join(x for x in (str(item.get("action", "")).strip(),
                                  " ".join(str(line.get("text", "")).strip()
                                           for line in item.get("dialogue", []))) if x)))
                     for item, before, floor in zip(result, original, speech_floors)]
        durations = list(speech_floors)
    difference = target - sum(durations)
    while difference:
        if difference > 0:
            candidates = [index for index, value in enumerate(durations) if value < MAX_SECONDS]
            if not candidates:
                raise ValueError("The episode target exceeds the available clip duration.")
            # First satisfy the action/dialogue preference, then spread any
            # remaining time evenly instead of padding the earliest clip.
            index = max(candidates, key=lambda value: (
                preferred[value] - durations[value], -durations[value], -value))
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
    expanded_labels = {
        "zh-CN": "为保留完整对白，本集生成时长由 {requested} 秒自动放宽至 {target} 秒",
        "zh-TW": "為保留完整對白，本集生成時長由 {requested} 秒自動放寬至 {target} 秒",
        "ja": "台詞を省略しないため、生成尺を{requested}秒から{target}秒へ自動延長",
        "en": "Preserved exact dialogue by extending generated duration from {requested}s to {target}s",
    }
    for item, before, duration in zip(result, original, durations):
        item["duration"] = duration
        if before != duration:
            note = labels.get(language, labels["en"]).format(duration=duration, target=target)
            item["duration_reason"] = (item.get("duration_reason", "").rstrip(".。；; ") + "; " + note).lstrip("; ")
    if target > requested_target and result:
        note = expanded_labels.get(language, expanded_labels["en"]).format(
            requested=requested_target, target=target)
        result[0]["duration_reason"] = (
            result[0].get("duration_reason", "").rstrip(".。；; ") + "; " + note).lstrip("; ")
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


def _compact_fallback_field(value, limit):
    """Keep a fallback field valid without pretending to replace its source.

    The immutable source manifest retains every paragraph/event and exact
    dialogue line. These prose fields are only render-facing summaries; a long
    timed block must not crash the emergency planner merely because its action
    notes exceed the local segment schema.
    """
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    marker = "\n…\n"
    available = max(2, limit - len(marker))
    head_size = int(available * .7)
    tail_size = available - head_size

    def boundary(text, preferred, reverse=False):
        window = text[max(0, preferred - 160):min(len(text), preferred + 160)]
        matches = list(re.finditer(r"[。！？!?；;\n]", window))
        if not matches:
            return preferred
        positions = [max(0, preferred - 160) + match.end() for match in matches]
        return (min(positions, key=lambda item: abs(item - preferred)) if not reverse
                else max(positions, key=lambda item: (item <= preferred, -abs(item - preferred))))

    head_end = boundary(value, head_size)
    tail_start = max(head_end, len(value) - tail_size)
    # The exact field ceiling is more important than a pretty boundary. Trim a
    # few excess characters after seeking sentence punctuation.
    result = value[:head_end].rstrip() + marker + value[tail_start:].lstrip()
    return result[:limit]


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
            compact = _compact_fallback_field(group["text"], 3000)
            result.append({
                "title": f"片段 {index + 1}", "story": compact, "setting": "",
                "action": compact, "ending": "动作完成并保持可衔接的画面状态",
                "duration": max(PLANNED_MIN_SECONDS, min(MAX_SECONDS, group["duration"])),
                "duration_reason": f"依据原剧本 {group['start']}–{group['end']} 秒自然节拍",
                "dialogue": dialogue, "image_prompt": compact,
                # This is an emergency heuristic, not an authored declaration
                # that the physical cast is empty.  Leave the timeline absent
                # so apply_plan can seed it from exact visible card mentions
                # and canonical dialogue speakers.  An explicit empty timeline
                # remains reserved for intentional display/off-screen shots.
                "transition_mode": "hard_cut",
                "device_view": "auto",
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
        compact = _compact_fallback_field(value, 3000)
        result.append({
            "title": f"片段 {index + 1}", "story": compact, "setting": "",
            "action": compact, "ending": "动作完成并保持可衔接的画面状态",
            "duration": duration,
            "duration_reason": f"本地估算：对白、动作与停顿合计约 {duration} 秒",
            "dialogue": [], "image_prompt": compact,
            "transition_mode": "hard_cut",
            "device_view": "auto",
            "card_selection": {kind: [] for kind in SELECTABLE_CARD_KINDS}})
    return fit_planned_durations(result, target_seconds, language) if target_seconds is not None else result


def _segment_hash_context(segment, *, keep_empty_prop_state=False):
    keys = ("title", "story", "setting", "action", "ending", "duration", "dialogue", "card_selection",
            "cast_timeline", "transition_mode", "device_view", "source_refs", "continuity_state", "shot_contract",
            "prompt_direction", "quality_repair_direction", "keyframe_asset_ids", "workflow_profile_id")
    context = copy.deepcopy({key: segment.get(key) for key in keys})
    # ``state`` was added to prop-holder rows after existing H3 projects had
    # already recorded their source hash. Normalisation necessarily supplies an
    # empty value for those old rows, but an empty migration field is not an
    # authored storyboard change and must not make every saved clip stale.
    if not keep_empty_prop_state:
        continuity = context.get("continuity_state")
        if isinstance(continuity, dict):
            for key in ("prop_holders_start", "prop_holders_end"):
                for row in continuity.get(key, []) if isinstance(continuity.get(key), list) else []:
                    if isinstance(row, dict) and not str(row.get("state") or "").strip():
                        row.pop("state", None)
    if segment.get("continue_previous") is False:
        context["continue_previous"] = False
    return context


def segment_hash(segment):
    context = _segment_hash_context(segment)
    return _hash(context)


def _segment_hash_with_empty_prop_state(segment):
    """Compatibility hash written briefly by the prop-state schema upgrade."""
    return _hash(_segment_hash_context(segment, keep_empty_prop_state=True))


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


def normalise_automation(value):
    """Validate the durable episode-run cursor without trusting browser state."""
    if not isinstance(value, dict):
        value = {}
    status = _text(value.get("status"), "automation status", 32, "idle") or "idle"
    stage = _text(value.get("stage"), "automation stage", 32, "idle") or "idle"
    if status not in AUTOMATION_STATUSES:
        status = "idle"
    if stage not in AUTOMATION_STAGES:
        stage = "idle"
    result = {
        "status": status,
        "stage": stage,
        "stage_detail": _text(value.get("stage_detail"), "automation detail", 300),
        "merge": value.get("merge", True),
        "requested_by": _text(value.get("requested_by"), "automation requester", 32, "user") or "user",
        "current_segment_id": value.get("current_segment_id") or None,
        "current_index": value.get("current_index", 0),
        "completed": value.get("completed", 0),
        "total": value.get("total", 0),
        "attempt": value.get("attempt", 0),
        "run_id": value.get("run_id") or None,
        "request_id": value.get("request_id") or None,
        "last_error": _text(value.get("last_error"), "automation error", 2000),
        "started_at": value.get("started_at"),
        "updated_at": value.get("updated_at"),
        "finished_at": value.get("finished_at"),
    }
    if type(result["merge"]) is not bool:
        raise ValueError("Automation merge must be true or false.")
    for key in ("current_index", "completed", "total", "attempt"):
        if type(result[key]) is not int or result[key] < 0 or result[key] > 10000:
            raise ValueError(f"Automation {key} must be a non-negative integer.")
    for key in ("current_segment_id", "run_id", "request_id"):
        if result[key]:
            safe_id(result[key])
    for key in ("started_at", "updated_at", "finished_at"):
        raw = result[key]
        if raw is not None and (type(raw) not in (int, float) or not math.isfinite(raw) or raw < 0):
            raise ValueError(f"Automation {key} must be a non-negative timestamp.")
        result[key] = round(float(raw), 3) if raw is not None else None
    return result


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
    shot_contract = normalise_shot_contract(
        value.get("shot_contract", prior.get("shot_contract")), value)
    transition_mode = reconcile_transition_contract(
        transition_mode, shot_contract.get("relation_previous"), index)
    shot_contract["relation_previous"] = transition_mode
    device_view = _text(value.get("device_view", prior.get("device_view")),
                        "device shot mode", 32, "auto") or "auto"
    if device_view not in DEVICE_VIEW_MODES:
        device_view = "auto"
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
        "device_view": device_view,
        "continue_previous": value.get(
            "continue_previous", prior.get("continue_previous", transition_mode == "continuous")),
        "duration_reason": _text(value.get("duration_reason"), "duration reason", 500),
        "dialogue": [],
        "image_prompt": _text(value.get("image_prompt"), "image prompt", 3000),
        "prompt_direction": _text(value.get("prompt_direction", prior.get("prompt_direction")),
                                  "video prompt revision", 6000),
        "quality_repair_direction": _text(
            value.get("quality_repair_direction", prior.get("quality_repair_direction")),
            "post-render repair direction", 2000),
        "quality_repair_count": value.get(
            "quality_repair_count", prior.get("quality_repair_count", 0)),
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
        "source_refs": normalise_source_refs(value.get("source_refs", prior.get("source_refs"))),
        "source_contract_version": value.get("source_contract_version",
                                             prior.get("source_contract_version", 0)),
        "continuity_state": normalise_continuity_state(
            value.get("continuity_state", prior.get("continuity_state")), value),
        "shot_contract": shot_contract,
        "coverage_issues": list(value.get("coverage_issues", prior.get("coverage_issues", [])) or []),
        "continuity_issues": list(value.get("continuity_issues", prior.get("continuity_issues", [])) or []),
        "preflight_issues": list(value.get("preflight_issues", prior.get("preflight_issues", [])) or []),
        "mmh3_allowed": bool(value.get("mmh3_allowed", prior.get("mmh3_allowed", False))),
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
        "ending_continuity_asset_id": prior.get("ending_continuity_asset_id"),
        "stale_reasons": list(prior.get("stale_reasons", [])),
        "selected_video_run_id": prior.get("selected_video_run_id"),
        "last_video_run_id": prior.get("last_video_run_id"),
    }
    if result["selected_video_run_id"]:
        safe_id(result["selected_video_run_id"])
    if (type(result["quality_repair_count"]) is not int or
            not 0 <= result["quality_repair_count"] <= 100):
        raise ValueError("Post-render repair count must be a non-negative integer.")
    if type(result["continue_previous"]) is not bool:
        raise ValueError("Clip continuation choice must be true or false.")
    if (not isinstance(result["continuity_warnings"], list) or
            not all(isinstance(item, str) for item in result["continuity_warnings"])):
        raise ValueError("Clip continuity warnings must be text entries.")
    result["continuity_warnings"] = [item[:360] for item in result["continuity_warnings"][:16]]
    for key in ("coverage_issues", "continuity_issues", "preflight_issues"):
        if not isinstance(result[key], list):
            result[key] = []
        result[key] = [item for item in result[key][:32] if isinstance(item, dict)]
    if result["last_video_run_id"]:
        safe_id(result["last_video_run_id"])
    if result["ending_continuity_asset_id"]:
        safe_id(result["ending_continuity_asset_id"])
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
    if type(result["source_contract_version"]) is not int or result["source_contract_version"] < 0:
        result["source_contract_version"] = 0
    dialogue = value.get("dialogue", [])
    if not isinstance(dialogue, list) or len(dialogue) > 16:
        raise ValueError("A clip can contain at most sixteen dialogue events.")
    for line in dialogue:
        if not isinstance(line, dict) or type(line.get("voiceover", False)) is not bool:
            raise ValueError("Dialogue entries must contain speaker, text, language and voiceover.")
        # Repair projects created before editorial headings were excluded from
        # screenplay dialogue.  A heading such as ``Part 5: “I Love You”`` is
        # metadata plus printed prop copy, not a person or an audible line.
        if editorial_speaker_label(line.get("speaker")):
            continue
        result["dialogue"].append({
            "speaker": _text(line.get("speaker"), "dialogue speaker", 100),
            "text": _text(line.get("text"), "dialogue text", 1000),
            "language": _text(line.get("language"), "dialogue language", 60, "Chinese") or "Chinese",
            "voiceover": line.get("voiceover", False)})
    if result["project_id"] and result["source_hash"] != segment_hash(result):
        result["status"] = "stale"
        result["stale_reasons"] = ["分镜内容或时长在 H3 工程创建后发生了变化"]
    return result


def _contract_issue(severity, code, message):
    return {"severity": severity, "code": code, "message": str(message)[:600]}


def _flatten_manifest(manifest, kind):
    return [row for chunk in manifest.get("chunks", []) for row in chunk.get(kind, [])]


def _distribute_source_refs(manifest, segments):
    """Backfill legacy/fallback clips in source order without inventing coverage."""
    if not segments:
        return
    for segment in segments:
        segment["source_refs"] = {key: [] for key in SOURCE_REF_KEYS}
    paragraphs = _flatten_manifest(manifest, "paragraphs")
    count = len(segments)
    paragraph_owner = {}
    for index, row in enumerate(paragraphs):
        owner = min(count - 1, int(index * count / max(1, len(paragraphs))))
        paragraph_owner[row["id"]] = owner
        segments[owner]["source_refs"]["paragraph_ids"].append(row["id"])
        scene_id = row["scene_id"]
        if scene_id not in segments[owner]["source_refs"]["scene_ids"]:
            segments[owner]["source_refs"]["scene_ids"].append(scene_id)
    for kind, key in (("dialogue", "dialogue_ids"), ("events", "event_ids")):
        rows = _flatten_manifest(manifest, kind)
        for index, row in enumerate(rows):
            owner = paragraph_owner.get(
                row.get("paragraph_id"), min(count - 1, int(index * count / max(1, len(rows)))))
            segments[owner]["source_refs"][key].append(row["id"])


def bind_planned_source_contract(production, planned, planner):
    """Validate model bindings; only deterministic fallback/legacy plans are auto-bound."""
    manifest = production_source_manifest(production)
    expected = {"scene_ids": [row["id"] for row in _flatten_manifest(manifest, "scenes")],
                "paragraph_ids": [row["id"] for row in _flatten_manifest(manifest, "paragraphs")],
                "dialogue_ids": [row["id"] for row in _flatten_manifest(manifest, "dialogue")],
                "event_ids": [row["id"] for row in _flatten_manifest(manifest, "events")]}
    # Direct API callers and productions created before this contract have no
    # source_refs field at all. Treat that as a legacy plan and bind it
    # deterministically. New planner responses are schema-required to include
    # the field, so an incomplete new response is still rejected below.
    if planner != "local_ai" or not any("source_refs" in item for item in planned):
        _distribute_source_refs(manifest, planned)
        return manifest
    for item in planned:
        item["source_refs"] = normalise_source_refs(item.get("source_refs"))
    issues = []
    for key in ("paragraph_ids", "dialogue_ids", "event_ids"):
        actual = [value for item in planned for value in item["source_refs"][key]]
        missing = [value for value in expected[key] if value not in actual]
        duplicates = list(dict.fromkeys(value for value in actual if actual.count(value) > 1))
        unknown = [value for value in actual if value not in expected[key]]
        if missing:
            issues.append(f"missing {key}: {', '.join(missing[:12])}")
        if duplicates:
            issues.append(f"duplicated {key}: {', '.join(duplicates[:12])}")
        if unknown:
            issues.append(f"unknown {key}: {', '.join(unknown[:12])}")
        if not missing and not duplicates and not unknown and actual != expected[key]:
            issues.append(f"reordered {key}")
    known_scenes = set(expected["scene_ids"])
    for item in planned:
        unknown = [value for value in item["source_refs"]["scene_ids"] if value not in known_scenes]
        if unknown:
            issues.append("unknown scene_ids: " + ", ".join(unknown[:12]))
        if not item["source_refs"]["scene_ids"] or not item["source_refs"]["paragraph_ids"]:
            issues.append("every clip must bind at least one original scene and paragraph")
        if not item["source_refs"]["dialogue_ids"] and not item["source_refs"]["event_ids"]:
            issues.append("every clip must own at least one authored dialogue line or plot event")
    if issues:
        raise ValueError(
            "The storyboard did not preserve the complete source coverage contract: " + "; ".join(issues))
    return manifest


def validate_planned_chunk_source_contract(story, chunk_index, planned):
    """Reject a locally generated part before it can contaminate later parts.

    A response can be valid, schema-conforming JSON and still omit the tail of
    the source manifest when a local model runs short of output tokens. Waiting
    until all parts are joined makes the resulting error hard to repair. This
    check gives the caller one bounded retry for the affected part only.
    """
    if not isinstance(planned, list) or not planned:
        raise ValueError(f"Storyboard part {chunk_index} returned no clips.")
    manifest = source_manifest_for_text(story, chunk_index)
    expected = {
        "paragraph_ids": [row["id"] for row in manifest.get("paragraphs", [])],
        "dialogue_ids": [row["id"] for row in manifest.get("dialogue", [])],
        "event_ids": [row["id"] for row in manifest.get("events", [])],
    }
    issues = []
    cleaned = []
    for index, segment in enumerate(planned):
        if not isinstance(segment, dict):
            raise ValueError(f"Storyboard part {chunk_index} contains an invalid clip.")
        # Run the same field-length/type validation used by the final save now,
        # while this single local-model part can still be retried. In
        # particular, verbose models sometimes copy the whole screenplay into
        # ``story`` despite the schema's 3,000-character ceiling.
        try:
            segment = normalise_segment(segment, index)
        except ValueError as exc:
            raise ValueError(f"Storyboard part {chunk_index} has an invalid clip: {exc}") from exc
        cleaned.append(segment)
    planned[:] = cleaned
    for key, expected_ids in expected.items():
        actual = [ident for segment in planned for ident in segment["source_refs"][key]]
        missing = [ident for ident in expected_ids if ident not in actual]
        duplicates = list(dict.fromkeys(ident for ident in actual if actual.count(ident) > 1))
        unknown = [ident for ident in actual if ident not in expected_ids]
        if missing:
            issues.append(f"missing {key}: {', '.join(missing[:20])}")
        if duplicates:
            issues.append(f"duplicated {key}: {', '.join(duplicates[:20])}")
        if unknown:
            issues.append(f"unknown {key}: {', '.join(unknown[:20])}")
        if not missing and not duplicates and not unknown and actual != expected_ids:
            issues.append(f"reordered {key}")
    for segment in planned:
        refs = segment["source_refs"]
        if not refs["scene_ids"] or not refs["paragraph_ids"]:
            issues.append("a clip is not bound to an original scene and paragraph")
        if not refs["dialogue_ids"] and not refs["event_ids"]:
            issues.append("a clip owns neither authored dialogue nor a plot event")
    if issues:
        raise ValueError(f"Storyboard part {chunk_index} did not preserve source coverage: " +
                         "; ".join(dict.fromkeys(issues)))
    return planned


def audit_storyboard_contract(production, *, backfill_legacy=True):
    """Derive coverage and adjacent-continuity reports for every saved clip."""
    manifest = production_source_manifest(production)
    segments = production.get("segments", [])
    if backfill_legacy and segments and not any(
            any(segment.get("source_refs", {}).get(key, []) for key in SOURCE_REF_KEYS)
            for segment in segments):
        _distribute_source_refs(manifest, segments)
    expected_rows = {kind: _flatten_manifest(manifest, kind)
                     for kind in ("scenes", "paragraphs", "dialogue", "events")}
    expected_ids = {"scene_ids": [row["id"] for row in expected_rows["scenes"]],
                    "paragraph_ids": [row["id"] for row in expected_rows["paragraphs"]],
                    "dialogue_ids": [row["id"] for row in expected_rows["dialogue"]],
                    "event_ids": [row["id"] for row in expected_rows["events"]]}
    coverage_issues = []
    for segment in segments:
        segment["coverage_issues"] = []
    strict_contract_active = any(
        segment.get("source_contract_version", 0) >= STORY_CONTRACT_VERSION for segment in segments)
    if strict_contract_active:
        for segment in segments:
            refs = segment.get("source_refs", {})
            local = []
            if not refs.get("scene_ids") or not refs.get("paragraph_ids"):
                local.append(_contract_issue(
                    "error", "unbound_clip",
                    f"Clip {segment.get('index')} is not bound to an original scene and paragraph."))
            if not refs.get("dialogue_ids") and not refs.get("event_ids"):
                local.append(_contract_issue(
                    "error", "unowned_story_beat",
                    f"Clip {segment.get('index')} owns no original dialogue line or plot event."))
            segment["coverage_issues"].extend(local)
            coverage_issues.extend(local)
    for key in ("paragraph_ids", "dialogue_ids", "event_ids"):
        actual = [value for segment in segments for value in segment.get("source_refs", {}).get(key, [])]
        missing = [value for value in expected_ids[key] if value not in actual]
        duplicates = list(dict.fromkeys(value for value in actual if actual.count(value) > 1))
        unknown = [value for value in actual if value not in expected_ids[key]]
        if missing:
            coverage_issues.append(_contract_issue("error", "missing_source",
                                                   f"Missing {key}: {', '.join(missing[:16])}"))
        if duplicates:
            coverage_issues.append(_contract_issue("error", "duplicate_source",
                                                   f"Duplicated {key}: {', '.join(duplicates[:16])}"))
        if unknown:
            coverage_issues.append(_contract_issue("error", "unknown_source",
                                                   f"Unknown {key}: {', '.join(unknown[:16])}"))
        if not missing and not duplicates and not unknown and actual != expected_ids[key]:
            coverage_issues.append(_contract_issue("error", "source_order",
                                                   f"{key} no longer follows the source order."))
    actual_dialogue = [line for segment in segments for line in segment.get("dialogue", [])]
    expected_dialogue = expected_rows["dialogue"]
    alias_owner = {
        alias: card["name"].strip().casefold()
        for card in production.get("cards", {}).get("characters", [])
        for alias in character_aliases(production).get(card.get("id"), set())
    }
    alias_owner.update({alias: card["name"].strip().casefold()
                        for alias, card in voice_character_aliases(production).items()})

    def audited_speaker(value):
        value = re.split(r"[|｜]", str(value or ""), maxsplit=1)[0].strip().casefold()
        return alias_owner.get(value, value)
    strict_dialogue_contract = bool(segments) and strict_contract_active
    if strict_dialogue_contract and len(actual_dialogue) != len(expected_dialogue):
        coverage_issues.append(_contract_issue(
            "error", "dialogue_count",
            f"Source has {len(expected_dialogue)} dialogue lines but the storyboard has {len(actual_dialogue)}."))
    for index, (source, actual) in enumerate(
            zip(expected_dialogue, actual_dialogue) if strict_dialogue_contract else [], 1):
        source_speaker = audited_speaker(source.get("speaker", ""))
        actual_speaker = audited_speaker(actual.get("speaker", ""))
        if source_speaker != actual_speaker:
            coverage_issues.append(_contract_issue(
                "error", "wrong_speaker", f"Dialogue line {index} changed speaker from {source['speaker']} to {actual.get('speaker') or 'unknown'}."))
        if (_dialogue_already_matches_language(source.get("text", ""), production.get("language", "zh-CN")) and
                source.get("text", "") != actual.get("text", "")):
            coverage_issues.append(_contract_issue(
                "error", "changed_dialogue", f"Dialogue line {index} no longer matches the authored wording."))
        if editorial_speaker_label(actual.get("speaker")):
            coverage_issues.append(_contract_issue(
                "error", "heading_as_dialogue", f"A section heading was treated as dialogue in line {index}."))
    # Attach global issues to the most relevant clip while keeping a compact
    # production summary for the episode-level admission gate.
    for issue in coverage_issues:
        if segments and issue not in segments[0]["coverage_issues"]:
            segments[0]["coverage_issues"].append(issue)
    production["source_manifest"] = manifest
    production["coverage_report"] = {
        "status": "error" if any(row["severity"] == "error" for row in coverage_issues) else "ok",
        "issues": coverage_issues,
        "counts": {"scenes": len(expected_rows["scenes"]),
                   "paragraphs": len(expected_rows["paragraphs"]),
                   "dialogue": len(expected_dialogue), "events": len(expected_rows["events"])},
    }

    continuity_issues = []
    previous = None
    transfer_cue = re.compile(r"\b(?:give|gave|hand|pass|take|receive|transfer|place|put)\b|"
                              r"递|交给|接过|拿走|放下|传给|渡す|受け取|置く", re.IGNORECASE)
    for segment in segments:
        issues = []
        roles = character_presence_roles(production, segment)
        timeline = effective_cast_timeline(production, segment, roles)
        state = normalise_continuity_state(segment.get("continuity_state"), segment)
        shot_contract = normalise_shot_contract(segment.get("shot_contract"), segment)
        segment["continuity_state"] = state
        segment["shot_contract"] = shot_contract
        if shot_contract["relation_previous"] != segment.get("transition_mode"):
            issues.append(_contract_issue(
                "error", "cut_relation_conflict",
                "shot_contract.relation_previous disagrees with transition_mode."))
        if previous is not None and segment.get("transition_mode") == "continuous":
            prior_timeline = effective_cast_timeline(production, previous)
            prior_end = {name.casefold(): name for name in prior_timeline["visible_end"]}
            current_start = {name.casefold(): name for name in timeline["visible_start"]}
            added = [current_start[key] for key in current_start.keys() - prior_end.keys()]
            missing = [prior_end[key] for key in prior_end.keys() - current_start.keys()]
            if added:
                issues.append(_contract_issue("error", "sudden_character_addition",
                                              "Continuous clip adds characters at its opening: " + ", ".join(added)))
            if missing:
                issues.append(_contract_issue("error", "sudden_character_disappearance",
                                              "Continuous clip loses characters at its opening: " + ", ".join(missing)))
            remote_before = {name.casefold() for name in
                             prior_timeline["offscreen"] + prior_timeline["mentioned_only"]}
            remote_now = [name for name in timeline["visible_start"] if name.casefold() in remote_before]
            if remote_now:
                issues.append(_contract_issue("error", "remote_became_physical",
                                              "Remote/off-screen identities entered the real space without a cut: " +
                                              ", ".join(remote_now)))
            prior_positions = {row["character"].casefold(): row for row in
                               previous.get("continuity_state", {}).get("positions_end", [])}
            for row in state["positions_start"]:
                old = prior_positions.get(row["character"].casefold())
                if old and ((old.get("position") and row.get("position") and old["position"] != row["position"]) or
                            (old.get("facing") and row.get("facing") and old["facing"] != row["facing"])):
                    issues.append(_contract_issue(
                        "error", "spatial_conflict",
                        f"{row['character']} changes position/facing across a continuous cut without an authored move."))
                if (old and old.get("movement_direction") and row.get("movement_direction") and
                        old["movement_direction"] != row["movement_direction"]):
                    issues.append(_contract_issue(
                        "error", "movement_direction_conflict",
                        f"{row['character']} reverses movement direction across a continuous cut."))
                if (old and old.get("eyeline_direction") and row.get("eyeline_direction") and
                        old["eyeline_direction"] != row["eyeline_direction"]):
                    issues.append(_contract_issue(
                        "error", "eyeline_conflict",
                        f"{row['character']}'s eyeline direction flips across a continuous cut."))
            prior_props = {row["prop"].casefold(): row["holder"] for row in
                           previous.get("continuity_state", {}).get("prop_holders_end", [])}
            prior_prop_rows = {row["prop"].casefold(): row for row in
                               previous.get("continuity_state", {}).get("prop_holders_end", [])}
            for row in state["prop_holders_start"]:
                holder = prior_props.get(row["prop"].casefold())
                if holder and holder.casefold() != row["holder"].casefold() and not transfer_cue.search(
                        "\n".join(str(segment.get(key, "")) for key in ("story", "action"))):
                    issues.append(_contract_issue(
                        "error", "prop_changed_hands",
                        f"{row['prop']} changes holder from {holder} to {row['holder']} without a handoff."))
                old = prior_prop_rows.get(row["prop"].casefold())
                if (old and old.get("state") and row.get("state") and
                        old["state"].strip().casefold() != row["state"].strip().casefold()):
                    issues.append(_contract_issue(
                        "error", "prop_state_jump",
                        f"{row['prop']} changes visible state across a continuous boundary "
                        f"({old['state']} -> {row['state']}). Keep phone orientation, screen direction, "
                        "open/closed state and hand use continuous, or author a motivated cut."))
            previous_selection = previous.get("card_selection", {})
            current_selection = segment.get("card_selection", {})
            prior_wardrobe = {str(value).strip().casefold()
                              for value in previous_selection.get("wardrobe", []) if str(value).strip()}
            current_wardrobe = {str(value).strip().casefold()
                                for value in current_selection.get("wardrobe", []) if str(value).strip()}
            if prior_wardrobe and current_wardrobe and prior_wardrobe != current_wardrobe:
                issues.append(_contract_issue(
                    "error", "wardrobe_jump",
                    "Wardrobe-card selection changes across a continuous boundary. Preserve the same worn "
                    "wardrobe, or use a state-change/time-jump cut."))
            prior_environments = {str(value).strip().casefold()
                                  for value in previous_selection.get("environments", []) if str(value).strip()}
            current_environments = {str(value).strip().casefold()
                                    for value in current_selection.get("environments", []) if str(value).strip()}
            if (prior_environments and current_environments and
                    prior_environments != current_environments):
                issues.append(_contract_issue(
                    "warning", "environment_boundary_change",
                    "Environment-card selection changes during continuous action. Confirm the move is visibly "
                    "motivated (for example through a doorway), otherwise use a scene-change cut."))
            if (str(previous.get("action", "")).strip() and
                    str(previous.get("action", "")).strip().casefold() == str(segment.get("action", "")).strip().casefold()):
                issues.append(_contract_issue("warning", "repeated_action",
                                              "This clip repeats the preceding clip's action verbatim."))
            previous_shot = previous.get("shot_contract", {})
            if (previous_shot.get("camera_axis") and shot_contract.get("camera_axis") and
                    previous_shot["camera_axis"] != shot_contract["camera_axis"] and
                    not shot_contract["allow_axis_cross"]):
                issues.append(_contract_issue(
                    "error", "axis_crossing",
                    "The camera axis changes during continuous action without permission to cross the axis."))
        if previous is not None and segment.get("transition_mode") in ("hard_cut", "matched_cut"):
            previous_shot = previous.get("shot_contract", {})
            if (shot_contract.get("shot_size") == previous_shot.get("shot_size") and
                    shot_contract.get("opening_composition") and
                    shot_contract.get("opening_composition") == previous_shot.get("ending_composition") and
                    segment.get("transition_mode") != "matched_cut"):
                issues.append(_contract_issue(
                    "warning", "accidental_jump_cut",
                    "The new shot repeats the preceding size and composition; change framing or use an intentional match."))
        hard_errors = any(row["severity"] == "error" for row in issues)
        mmh3_allowed = bool(previous is not None and segment.get("transition_mode") == "continuous" and
                            state.get("mmh3_eligible") and not hard_errors)
        if production.get("auto_continue_previous") and segment.get("continue_previous") and not mmh3_allowed:
            issues.append(_contract_issue("error", "invalid_mmh3_continuation",
                                          "Previous-ending continuation is enabled but this transition is not MMH3-safe."))
        segment["mmh3_allowed"] = mmh3_allowed
        segment["continuity_issues"] = issues
        continuity_issues.extend({**row, "segment_index": segment.get("index")} for row in issues)
        previous = segment
    production["continuity_report"] = {
        "status": "error" if any(row["severity"] == "error" for row in continuity_issues) else
                  "warning" if continuity_issues else "ok",
        "issues": continuity_issues,
    }
    audit_shot_preflight(production)
    return production


def _preflight_text(segment):
    return "\n".join(str(segment.get(key) or "") for key in
                     ("story", "setting", "action", "ending", "prompt_direction"))


def _explicit_cut_count(text):
    # Count authored editorial changes, not ordinary uses of words such as
    # "cut paper". A production clip is one H3 render and therefore should not
    # quietly contain a miniature edit sequence.
    return len(re.findall(
        r"\b(?:cut\s+to|smash\s+cuts?|jump\s+cuts?|match[- ]cuts?|hard\s+cuts?|"
        r"insert\s+shot|reaction\s+shot|reverse\s+shot|"
        r"through\s+(?:a\s+)?(?:visual\s+)?match\s+cut|transitions?\s+to)\b|"
        r"切到|切至|镜头切换|鏡頭切換|匹配剪辑|匹配剪輯|カット(?:する|して)?|場面転換|マッチカット",
        text, re.IGNORECASE | re.MULTILINE))


def _hard_editorial_cut_count(text):
    """Count edits that cannot be safely restaged as one continuous H3 take.

    A single plain ``cut to`` inside one location is commonly model-authored
    shorthand for changing emphasis. Prompt generation can turn that into a
    pan, rack focus or reframing without changing story facts. Match, smash,
    jump and explicit hard/time/location transitions remain blocking.
    """
    return len(re.findall(
        r"\b(?:smash\s+cuts?|jump\s+cuts?|match[- ]cuts?|hard\s+cuts?|"
        r"through\s+(?:a\s+)?(?:visual\s+)?match\s+cut|transitions?\s+to)\b|"
        r"镜头切换|鏡頭切換|匹配剪辑|匹配剪輯|場面転換|マッチカット",
        text, re.IGNORECASE | re.MULTILINE))


def _camera_view_count(text):
    patterns = (
        r"\b(?:extreme\s+wide|wide\s+shot|full\s+shot|medium\s+shot|medium\s+close|"
        r"close[- ]?up|extreme\s+close|over[- ]the[- ]shoulder|overhead|top[- ]down|"
        r"low[- ]angle|high[- ]angle|pov|reverse\s+angle)\b",
        r"(?:大全景|远景|遠景|全景|中景|近景|特写|特寫|大特写|大特寫|过肩|過肩|"
        r"俯拍|仰拍|主观镜头|主觀鏡頭|クローズアップ|ロングショット|俯瞰|煽り)",
    )
    found = []
    for pattern in patterns:
        found.extend(match.group(0).casefold() for match in re.finditer(pattern, text, re.IGNORECASE))
    return len(dict.fromkeys(found))


def _action_beat_count(text):
    clauses = [row.strip() for row in re.split(
        r"(?:[.!?。！？;；\n]+|\b(?:then|afterwards|next|meanwhile|suddenly)\b|"
        r"然后|接着|随后|与此同时|突然|然後|接著|隨後|同時|それから|続いて|次に|突然)",
        text, flags=re.IGNORECASE) if row.strip()]
    return len(clauses)


def shot_preflight_for_segment(production, segment):
    """Return one compact, actionable pre-prompt quality report.

    Semantic facts come from the authored contracts; cheap text checks only
    flag render complexity. Warnings remain advisory. Errors are reserved for
    contradictions that cannot be rendered without changing the screenplay.
    """
    issues = []
    text = _preflight_text(segment)
    duration = int(segment.get("duration") or DEFAULT_CLIP_SECONDS)
    timeline = effective_cast_timeline(production, segment)
    visible_count = max(len(timeline.get("visible_start", [])),
                        len(timeline.get("visible_end", [])))
    # Story, setting and action commonly restate the same editorial idea.  The
    # number of authored edits is therefore the largest count in any one field,
    # not the sum of duplicate prose across all fields.  Two explicit cuts in
    # the action still block one H3 render, while one match-cut repeated in the
    # summary and setting remains one (still blocking) editorial boundary.
    preflight_fields = [str(segment.get(key) or "") for key in
                        ("story", "setting", "action", "ending", "prompt_direction")]
    cut_count = max((_explicit_cut_count(value) for value in preflight_fields), default=0)
    hard_cut_count = max((_hard_editorial_cut_count(value) for value in preflight_fields), default=0)
    camera_views = _camera_view_count(text)
    action_beats = _action_beat_count("\n".join(
        str(segment.get(key) or "") for key in ("action", "ending")))

    if action_beats > max(4, math.ceil(duration / 2)):
        issues.append(_contract_issue(
            "warning", "dense_action",
            f"Clip {segment.get('index')} packs about {action_beats} action beats into {duration}s; simplify or split it."))
    if cut_count > 1:
        issues.append(_contract_issue(
            "error", "multiple_internal_cuts",
            f"Clip {segment.get('index')} contains {cut_count} internal cuts, but one H3 clip must remain one filmable setup."))
    elif hard_cut_count:
        issues.append(_contract_issue(
            "error", "internal_editorial_cut",
            f"Clip {segment.get('index')} contains an editorial cut or match-cut inside one H3 render; split it into separate storyboard clips and join them in the final edit."))
    elif cut_count == 1:
        issues.append(_contract_issue(
            "warning", "internal_cut",
            f"Clip {segment.get('index')} contains one repairable internal cut cue; prompt generation must restage it as one continuous camera setup."))
    if camera_views >= 3:
        issues.append(_contract_issue(
            "error", "impossible_camera_change",
            f"Clip {segment.get('index')} asks one render to cover {camera_views} distinct camera views."))
    elif camera_views == 2:
        issues.append(_contract_issue(
            "warning", "camera_change",
            f"Clip {segment.get('index')} names two camera views; confirm the move is physically continuous."))

    face_front = re.search(
        r"(?:front[- ]facing|face\s+(?:toward|to)\s+(?:the\s+)?camera|正面脸|正面臉|正对镜头|"
        r"正對鏡頭|カメラ正面)", text, re.IGNORECASE)
    screen_front = re.search(
        r"(?:screen\s+(?:faces?|facing)\s+(?:the\s+)?camera|readable\s+(?:phone\s+)?screen|"
        r"phone\s+screen\s+front|屏幕正对镜头|螢幕正對鏡頭|手机屏幕正面|手機螢幕正面|"
        r"画面をカメラ正面)", text, re.IGNORECASE)
    if face_front and screen_front and segment.get("device_view") not in ("screen", "remote_panel"):
        issues.append(_contract_issue(
            "error", "face_screen_geometry",
            f"Clip {segment.get('index')} simultaneously requires a frontal face and frontal readable phone screen from one camera."))

    roles = character_presence_roles(production, segment)
    visible_labels = {str(name).strip().casefold() for name in
                      timeline.get("visible_start", []) + timeline.get("visible_end", [])}
    aliases = character_aliases(production)
    escaped_planes = []
    cards = {card["id"]: card for card in production.get("cards", {}).get("characters", [])}
    for card_id, role in roles.items():
        if role not in ("display", "imagined", "offscreen"):
            continue
        if any(alias in visible_labels for alias in aliases.get(card_id, set())):
            escaped_planes.append(cards.get(card_id, {}).get("name", card_id))
    if escaped_planes:
        issues.append(_contract_issue(
            "error", "nonphysical_character_in_reality",
            "Remote, remembered or off-screen characters were placed in the physical cast: " +
            ", ".join(escaped_planes[:8])))

    dialogue_floor = dialogue_minimum_seconds(segment)
    if segment.get("dialogue") and dialogue_floor > duration:
        modest_overrun = dialogue_floor <= duration + 2
        issues.append(_contract_issue(
            "warning" if modest_overrun else "error",
            "tight_dialogue" if modest_overrun else "dialogue_overflow",
            (f"Clip {segment.get('index')} has about {dialogue_floor}s of conservatively paced dialogue in a "
             f"{duration}s authored clip; use a naturally brisk delivery and avoid extra pauses."
             if modest_overrun else
             f"Clip {segment.get('index')} has at least {dialogue_floor}s of dialogue but only {duration}s available.")))
    if visible_count > 9:
        issues.append(_contract_issue(
            "error", "cast_capacity",
            f"Clip {segment.get('index')} asks for {visible_count} physical characters, beyond the nine-reference H3 budget."))
    elif visible_count > 4:
        issues.append(_contract_issue(
            "warning", "dense_cast",
            f"Clip {segment.get('index')} has {visible_count} visible characters; prefer an overview reference and restrained staging."))

    # Coverage and adjacent-state failures belong to the same user-facing
    # preflight instead of surfacing later as unrelated generation errors.
    inherited = [copy.deepcopy(row) for row in
                 list(segment.get("coverage_issues", [])) +
                 list(segment.get("continuity_issues", []))]
    result = inherited + issues
    deduped, seen = [], set()
    for row in result:
        key = (row.get("severity"), row.get("code"), row.get("message"))
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def audit_shot_preflight(production):
    all_issues = []
    global_coverage_errors = [copy.deepcopy(row) for row in
                              production.get("coverage_report", {}).get("issues", [])
                              if row.get("severity") == "error"]
    for segment in production.get("segments", []):
        segment["preflight_issues"] = global_coverage_errors + shot_preflight_for_segment(production, segment)
        unique, seen = [], set()
        for row in segment["preflight_issues"]:
            key = (row.get("severity"), row.get("code"), row.get("message"))
            if key not in seen:
                seen.add(key)
                unique.append(row)
        segment["preflight_issues"] = unique
        all_issues.extend({**row, "segment_index": segment.get("index")}
                          for row in segment["preflight_issues"])
    production["preflight_report"] = {
        "status": "error" if any(row.get("severity") == "error" for row in all_issues) else
                  "warning" if all_issues else "ok",
        "issues": all_issues,
        "checked_clips": len(production.get("segments", [])),
    }
    return production


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
        # Production planning and prompt generation can outlive the request that
        # started them.  Serialize file reads/writes so an explicit pause cannot
        # be lost when one of those older requests eventually saves its result.
        self.lock = threading.RLock()

    def _path(self, ident):
        return self.directory / (safe_id(ident) + ".json")

    def get(self, ident):
        with self.lock:
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
        result["auto_quality_review"] = result.get("auto_quality_review", True)
        if type(result["auto_quality_review"]) is not bool:
            raise ValueError("Automatic post-render quality review must be true or false.")
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
        result["automation"] = normalise_automation(result.get("automation"))
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
        # Reports are derived from the current episode source and current clip
        # order on every load/save. Legacy projects receive deterministic
        # source bindings once; partial or contradictory bindings remain
        # visible as errors instead of being silently repaired.
        audit_storyboard_contract(result, backfill_legacy=True)
        current_context_hash = production_context_hash(result)
        upstream_changed = False
        for segment in result["segments"]:
            reasons = []
            current_segment_hash = segment_hash(segment)
            # Accept and immediately rebase the short-lived upgrade hash that
            # included ``state: ""`` in legacy prop rows. The user did not edit
            # the storyboard, so loading or saving an old project must not start
            # an automatic prompt rebuild.
            if (segment["project_id"] and segment["source_hash"] and
                    segment["source_hash"] == _segment_hash_with_empty_prop_state(segment)):
                segment["source_hash"] = current_segment_hash
            source_changed = bool(segment["project_id"] and
                                  segment["source_hash"] != current_segment_hash)
            if source_changed:
                reasons.append("分镜内容或时长在 H3 工程创建后发生了变化")
            if segment["project_id"] and segment.get("context_hash") != current_context_hash:
                reasons.append("全片约束或资产卡已变化")
            # A reference allocator upgrade changes future renders, but it does
            # not invalidate an already adopted video. Keep the saved take
            # available and repair the one clip lazily when the user asks for a
            # new render; server-side admission still prevents submitting the
            # outdated project snapshot unchanged.
            if (segment["project_id"] and
                    segment.get("cast_timeline_version", 0) != CAST_TIMELINE_VERSION):
                reasons.append("角色入场、退场与结尾在场规则已升级，请先重新规划本集分镜")
            if segment["project_id"]:
                reasons.extend(segment_identity_repair_reasons(result, segment))
            own_change = bool(reasons)
            if own_change:
                segment["status"] = "stale"
                segment["stale_reasons"] = reasons
            elif segment["project_id"] and segment.get("status") == "stale":
                # Staleness is derived from the current production contract,
                # not a permanent flag. This also clears legacy global
                # reference-version warnings after an upgrade is installed.
                segment["status"] = "ready"
                segment["stale_reasons"] = []
            if upstream_changed and segment["project_id"] and not own_change:
                segment["status"] = "stale"
                segment["stale_reasons"] = ["前序分镜已变化，人物、道具或运动连续性需要重新确认"]
            # A changed story beat can alter downstream blocking.  A local
            # prompt/reference repair cannot, so never invalidate good later
            # clips merely because an earlier clip used a voice ID or lacked a
            # reference image.
            upstream_changed = upstream_changed or source_changed
        return result

    def save(self, value, *, allow_task_state_transition=False):
        with self.lock:
            result = self.validate(value)
            path = self._path(result["id"])
            if path.is_file() and not allow_task_state_transition:
                persisted = json.loads(path.read_text(encoding="utf-8"))
                # A long-running request may hold an older active snapshot.  Once
                # the user pauses the production, only the explicit task-state
                # update below is allowed to resume it.
                if persisted.get("task_state") == "paused" and result["task_state"] == "active":
                    result["task_state"] = "paused"
            result["updated_at"] = time.time()
            atomic_json(path, result)
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
            "auto_quality_review": True,
            "auto_keyframes_enabled": False,
            "auto_keyframe_model": "z_image_turbo_bf16.safetensors",
            "video_aspect_ratio": body.get("video_aspect_ratio", source.get("aspect_ratio", "16:9")),
            "video_resolution": body.get("video_resolution", source.get("comfy_render", {}).get("resolution", "0.7")),
            "video_quality": body.get("video_quality", source.get("comfy_render", {}).get("quality", "fast")),
            "video_steps": body.get("video_steps", source.get("comfy_render", {}).get("steps", "auto")),
            "task_state": "active",
            "automation": normalise_automation(None),
            "timings": {key: None for key in TIMING_KEYS},
            "generated_overviews": {},
            "planner": None, "planner_warning": None, "segments": []}
        return self.save(result)

    def update(self, ident, body):
        current = self.get(ident)
        allowed = {"title", "language", "prompt_version", "brief", "style_bible", "character_bible", "continuity_notes", "cards", "segments", "auto_merge", "auto_continue_previous", "auto_quality_review", "task_state", "auto_keyframes_enabled", "auto_keyframe_model",
                   "video_aspect_ratio", "video_resolution", "video_quality", "video_steps",
                   "visual_style_preset", "visual_style_custom", "narrative_style", "narrative_style_custom", "narrative_notes", "episode_count", "episode_minutes",
                   "current_episode", "episodes", "card_collection_id", "card_collection_name", "overview_asset_ids", "series_voice_style"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError("Production update contains unsupported fields.")
        for key in allowed:
            if key in body:
                current[key] = copy.deepcopy(body[key])
        return self.save(current, allow_task_state_transition="task_state" in body)

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
        character_by_voice_alias = voice_character_aliases(current)
        planned = copy.deepcopy(planned)
        explicit_source_contract = any("source_refs" in item for item in planned)
        source_manifest = bind_planned_source_contract(current, planned, planner)
        if any(group.get("dialogue_parse_failed") for group in locked_groups):
            raise ValueError(
                "The source screenplay appears to contain quoted dialogue, but its speaker format could not be parsed. "
                "No storyboard was saved; review the speaker cue instead of silently losing dialogue.")

        def speaker_key(value):
            value = re.split(r"[|｜]", str(value or "").strip().casefold(), maxsplit=1)[0].strip()
            card = character_by_alias.get(value) or character_by_voice_alias.get(value)
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

        def planned_children_by_locked_group():
            """Map AI subdivisions back to each authored timing block."""
            if len(locked_groups) == len(planned):
                return [[item] for item in planned]
            markers = [item.get("_source_timed_group") for item in planned]
            if any(marker is not None for marker in markers):
                if not all(type(marker) is int for marker in markers):
                    raise ValueError("A planned timing block lost its source-group binding.")
                grouped = []
                cursor = 0
                for group_index, locked in enumerate(locked_groups, 1):
                    children = []
                    while cursor < len(planned) and markers[cursor] == group_index:
                        children.append(planned[cursor])
                        cursor += 1
                    if not children or sum(item["duration"] for item in children) != (
                            locked["end_seconds"] - locked["start_seconds"]):
                        raise ValueError(
                            "A subdivided plan no longer covers its authored timing block exactly.")
                    grouped.append(children)
                if cursor != len(planned):
                    raise ValueError("A subdivided plan reordered its authored timing blocks.")
                return grouped

            # Compatibility for callers and saved plans created before source
            # group tags existed: fitted child durations still identify the
            # ordered parent block without relying on model prose.
            grouped, cursor = [], 0
            for locked in locked_groups:
                required = locked["end_seconds"] - locked["start_seconds"]
                children, covered = [], 0
                while cursor < len(planned) and covered < required:
                    children.append(planned[cursor])
                    covered += planned[cursor]["duration"]
                    cursor += 1
                if not children or covered != required:
                    raise ValueError(
                        "A subdivided plan no longer covers its authored timing block exactly.")
                grouped.append(children)
            if cursor != len(planned):
                raise ValueError("A subdivided plan reordered its authored timing blocks.")
            return grouped

        def exact_dialogue_child(children, line_index, line_count):
            """Choose a stable child for a source line the model forgot."""
            total = sum(item["duration"] for item in children)
            point = total * (line_index + .5) / max(1, line_count)
            covered = 0
            for child_index, child in enumerate(children):
                covered += child["duration"]
                # A line exactly on a subdivision boundary belongs to the
                # following child, after the preceding visible setup.
                if point < covered or child_index == len(children) - 1:
                    return child_index
            return len(children) - 1

        def restore_exact_dialogue(children, locked):
            """Discard model dialogue drift and reattach source-exact lines."""
            expected = locked.get("source_dialogue", locked.get("dialogue", []))
            actual = [(child_index, line) for child_index, child in enumerate(children)
                      for line in child.get("dialogue", [])]
            for child in children:
                child["dialogue"] = []
            search_from, previous_child = 0, 0
            for line_index, source_line in enumerate(expected):
                selected, selected_position = None, None
                for position in range(search_from, len(actual)):
                    child_index, actual_line = actual[position]
                    if (child_index >= previous_child and
                            speaker_key(source_line.get("speaker")) == speaker_key(actual_line.get("speaker")) and
                            str(source_line.get("text", "")).strip() ==
                            str(actual_line.get("text", "")).strip()):
                        selected, selected_position = child_index, position
                        break
                if selected is None:
                    selected = max(previous_child, exact_dialogue_child(
                        children, line_index, len(expected)))
                else:
                    search_from = selected_position + 1
                previous_child = selected
                children[selected]["dialogue"].append(copy.deepcopy(source_line))

        if locked_groups:
            for children, locked in zip(planned_children_by_locked_group(), locked_groups):
                # The local model chooses staging and subdivision boundaries;
                # authored dialogue remains server-owned source material.  For
                # same-language scripts, repair omissions/paraphrases instead
                # of rejecting the entire storyboard.  Invented dialogue is
                # discarded, including in authored silent blocks.
                if not locked.get("requires_translation"):
                    restore_exact_dialogue(children, locked)
                    continue

                # Translation still needs the language model's words, but its
                # line count, speaker order and child placement are validated
                # per authored block rather than across the whole episode.
                counts = [len(child.get("dialogue", [])) for child in children]
                translated = translated_dialogue(
                    {"dialogue": [line for child in children for line in child.get("dialogue", [])]},
                    locked)
                offset = 0
                for child, count in zip(children, counts):
                    child["dialogue"] = translated[offset:offset + count]
                    offset += count
        # Apply the same exact-dialogue contract to ordinary scripts without
        # authored timecodes. source_refs decides which clip owns each line;
        # the model may translate words but never move, merge or reassign them.
        source_dialogue = {row["id"]: row for row in _flatten_manifest(source_manifest, "dialogue")}
        for item in planned if (explicit_source_contract or planner != "local_ai") else []:
            expected = [source_dialogue[value] for value in item.get("source_refs", {}).get("dialogue_ids", [])
                        if value in source_dialogue]
            exact = []
            requires_translation = False
            for row in expected:
                speaker = row["speaker"]
                folded = speaker.casefold()
                card = character_by_alias.get(folded) or character_by_voice_alias.get(folded)
                if card:
                    speaker = card["name"]
                exact.append({"speaker": speaker, "text": row["text"],
                              "language": PRODUCTION_LANGUAGES[current["language"]], "voiceover": False})
                requires_translation = requires_translation or not _dialogue_already_matches_language(
                    row["text"], current["language"])
            if not requires_translation or planner != "local_ai":
                item["dialogue"] = exact
            else:
                translated_dialogue(item, {"source_dialogue": exact})
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
                                 character_by_voice_alias.get(speaker_name) or
                                 character_by_alias.get(speaker_key) or character_by_name.get(speaker_key) or
                                 character_by_voice_alias.get(speaker_key))
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
                visible_speaker_keys = {name.casefold() for name in visible_speaker_names}
                # If the authored current clip visibly stages a speaking
                # character after an earlier story departure, the present-tense
                # performance is an implicit re-entry.  Do not let a missing
                # planner ``enters`` token erase the speaker and then emit the
                # contradictory pair "visible Subject" / "show exactly none".
                for name in temporal_visible:
                    folded = name.casefold()
                    if folded in departed_characters and folded in visible_speaker_keys:
                        if name not in timeline["enters"]:
                            timeline["enters"].append(name)
                            segment_corrections.append(
                                f"Clip {index + 1}: restored visible speaker {name} as an implicit re-entry.")
                        entering.add(folded)
                        departed_characters.discard(folded)
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
            item["source_contract_version"] = (
                STORY_CONTRACT_VERSION if explicit_source_contract or planner != "local_ai" else 0)
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
        self._repair_recoverable_preflight(production, segment)
        self.assert_storyboard_contract_current(production, segment)
        if segment.get("cast_timeline_version", 0) != CAST_TIMELINE_VERSION:
            raise ValueError(
                "This storyboard predates temporal cast tracking. Replan this episode before rebuilding its video prompts.")
        # Rebuilding one stale clip is sufficient for legacy voice-ID and
        # visible-speaker errors. Keep every unaffected prompt/take untouched.
        repair_segment_identity_contract(production, segment)
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
        # Reference-driven and text-only productions share the same card
        # library. A clip can therefore have strong written identity/style
        # direction without owning a renderable image or audio reference.
        # Ref2VA rejects that valid text-only clip, while treating a style
        # analysis image as a subject reference would leak its depicted
        # content into the scene. Select the effective H3 conditioning mode
        # from the clip's actual reference assets instead.
        if production.get("source_mode") in ("ref2va", "t2va"):
            has_reference_asset = bool(
                card_selection["image_asset_ids"] or card_selection["audio_asset_ids"])
            base["mode"] = "ref2va" if has_reference_asset else "t2va"
            card_selection["effective_mode"] = base["mode"]
            if base["mode"] == "t2va" and render.get("workflow_profile_id") == REF8_WORKFLOW_ID:
                # The optional eight-step LoRA graph is Ref2VA-only. Fall back
                # to the unchanged built-in workflow for this one text-only
                # clip instead of failing the whole episode. Restore any
                # source render values so the LoRA recipe cannot leak into it.
                source_render = source.get("comfy_render", {})
                for key in ("loras", "shift_video", "shift_audio"):
                    if key in source_render:
                        render[key] = copy.deepcopy(source_render[key])
                    else:
                        render.pop(key, None)
                render["workflow_profile_id"] = "builtin"
                render["quality"] = "fast" if production["video_quality"] == "lora8" else production["video_quality"]
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
        character_by_id = {card["id"]: card for card in production["cards"]["characters"]}
        character_by_speaker_alias = voice_character_aliases(production)
        for card_id, aliases in character_aliases(production).items():
            character = character_by_id.get(card_id)
            if character:
                for alias in aliases:
                    character_by_speaker_alias.setdefault(alias, character)
        physical = list(card_selection.get("physical_subject_ids", []))
        display = list(card_selection.get("display_subject_ids", []))
        imagined = list(card_selection.get("imagined_subject_ids", []))
        offscreen = list(card_selection.get("offscreen_subject_ids", []))
        lines, visible_dialogue, selected_voices, selected_voice_ids = [], [], [], set()
        collective_subjects = []
        selected_names = {card["name"].strip().casefold() for card in relevant_cards["characters"]}
        for line in segment["dialogue"]:
            key = line["speaker"].strip().casefold()
            character = character_by_speaker_alias.get(key)
            subject = names.get(character["name"].strip().casefold()) if character else names.get(key)
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
                role = card_selection.get("presence_roles_by_name", {}).get(
                    subject["name"].strip().casefold(), "")
                if line["voiceover"] or role == "offscreen":
                    offscreen.append(subject["id"])
                elif role == "display":
                    display.append(subject["id"])
                elif role == "imagined":
                    imagined.append(subject["id"])
                else:
                    visible_dialogue.append(subject["id"])
        # A reference may identify somebody confined to a phone/display or a
        # non-diegetic memory. Only the physical group enters the scene
        # contract; remote and imagined identities retain their reference
        # without becoming another body beside the local performer.
        visible = list(dict.fromkeys(physical + visible_dialogue))
        display = [subject_id for subject_id in dict.fromkeys(display)
                   if subject_id not in visible]
        imagined = [subject_id for subject_id in dict.fromkeys(imagined)
                    if subject_id not in visible and subject_id not in display]
        offscreen = [subject_id for subject_id in dict.fromkeys(offscreen)
                     if subject_id not in visible and subject_id not in display and subject_id not in imagined]
        offscreen_ids = set(offscreen)
        if not visible and not display and not imagined and len(base["subjects"]) == 1:
            visible = [s["id"] for s in base["subjects"]
                       if s["id"] not in offscreen_ids and not s.get("collective_member_ids")]
        visible = list(dict.fromkeys(visible))
        if collective_subjects and not visible:
            raise ValueError("Collective dialogue needs at least one selected visible character")
        for collective in collective_subjects:
            collective["collective_member_ids"] = list(visible)
        presence_roles = character_presence_roles(production, segment)
        cast_lock = effective_temporal_cast_lock(production, segment, presence_roles)
        prop_lock = inscribed_prop_continuity_lock(relevant_cards["props"])
        base["production_render_override"] = production_render_override(
            production, segment, relevant_cards, presence_roles)
        character_by_subject = {
            subject["id"]: subject["name"] for subject in base["subjects"]
            if subject.get("id") and subject.get("name")}
        final_labels = {str(name).strip().casefold()
                        for name in normalise_cast_timeline(
                            segment.get("cast_timeline"),
                            segment.get("card_selection", {}).get("characters", []))["visible_end"]}
        aliases_by_card = character_aliases(production)
        final_physical_names = [
            card["name"] for card in production["cards"]["characters"]
            if presence_roles.get(card["id"]) == "physical" and
            any(alias in final_labels for alias in aliases_by_card.get(card["id"], set()))]
        final_cast = ", ".join(final_physical_names) or "none"
        final_display = ", ".join(character_by_subject[sid] for sid in display
                                  if sid in character_by_subject) or "none"
        final_imagined = ", ".join(character_by_subject[sid] for sid in imagined
                                   if sid in character_by_subject) or "none"
        scene = shot(segment["duration"])
        scene.update({"action": "\n\n".join(x for x in [
                clip_text(segment["action"] or segment["story"]), cast_lock, prop_lock] if x),
            "setting": clip_text(segment["setting"]),
            "final_state": "\n\n".join(x for x in [
                clip_text(segment["ending"]),
                f"FINAL-FRAME CAST LOCK: show exactly {final_cast} as physical cast in the real location; every other named identity is physically absent.",
                f"FINAL-FRAME DISPLAY LOCK: {final_display} may appear only inside the declared bounded device/remote display.",
                f"FINAL-FRAME MEMORY LOCK: {final_imagined} may appear only as the declared non-diegetic thought/memory image."
            ] if x), "dialogue": lines,
            "visible_subject_ids": visible,
            "display_subject_ids": display,
            "imagined_subject_ids": imagined,
            "offscreen_subject_ids": offscreen,
            "director_locks": ["setting", "final_state", "visible_subject_ids",
                               "display_subject_ids", "imagined_subject_ids",
                               "offscreen_subject_ids"]})
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
            ("TEXT-ONLY VISIBLE IDENTITY WARNING: these selected characters have no dedicated image or approved character overview binding: "
             + ", ".join(card_selection.get("missing_visual_identity_names", []))
             + ". Their written cards remain authoritative, but stable appearance across separately rendered clips is not guaranteed; add a character image or approved overview before final rendering."
             if card_selection.get("missing_visual_identity_names") else ""),
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
             prop_lock,
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
            f"POST-RENDER QUALITY REPAIR: {segment['quality_repair_direction']}" if segment.get("quality_repair_direction") else "",
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
        digest = _hash({"overview_version": 3, "kind": kind, "assets": source_ids})
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
                prepared = _primary_character_view(source) if kind == "characters" else source.convert("RGB")
                image = ImageOps.contain(prepared, (cell - 12, cell - 12))
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

    def _ensure_character_identity(self, production, card):
        """Return a single-instance render asset without altering the card.

        One-to-three-character clips previously bypassed the automatic cast
        overview and sent full multi-view sheets straight to H3. Cache a
        cropped derivative when that layout is detected; ordinary portraits
        and scene references retain their original asset.
        """
        if not card.get("asset_ids"):
            return None
        source_id = card["asset_ids"][0]
        digest = _hash({"character_identity_version": 1, "card": card["id"], "asset": source_id})
        cached = production["generated_overviews"].get(digest)
        if cached:
            try:
                self.load_asset(cached)
                return cached
            except Exception:
                pass
        if not self.store_asset:
            return source_id
        from PIL import Image
        meta = self.load_asset(source_id)
        path = self.data_dir / "assets" / source_id / meta["filename"]
        with Image.open(path) as source:
            original = source.convert("RGB")
            prepared = _primary_character_view(original)
            if prepared.size == original.size:
                return source_id
            buffer = io.BytesIO()
            prepared.save(buffer, "JPEG", quality=95, optimize=True)
        asset = self.store_asset(
            buffer.getvalue(), f"character_identity_{digest[:12]}.jpg", "image/jpeg")
        production["generated_overviews"][digest] = asset["id"]
        return asset["id"]

    def _relevant_cards(self, production, segment):
        text = "\n".join(str(segment.get(key, "")) for key in
            ("title", "story", "setting", "action", "ending", "image_prompt")).casefold()
        speakers = {line.get("speaker", "").strip().casefold() for line in segment.get("dialogue", [])}
        alias_sets = character_aliases(production)
        voice_aliases = voice_character_aliases(production)
        voice_character_ids = {card["id"] for alias, card in voice_aliases.items() if alias in speakers}
        speaker_cards = {card["id"] for card in production["cards"]["characters"]
                         if (card["id"] in voice_character_ids or
                             any(alias in speakers for alias in alias_sets.get(card["id"], set())))}
        presence_roles = character_presence_roles(production, segment)
        visually_required = {card_id for card_id, role in presence_roles.items()
                             if role in ("physical", "display", "imagined")}
        if segment.get("card_selection_source") == "local_ai":
            selection = segment.get("card_selection", {})
            result = {}
            for kind in SELECTABLE_CARD_KINDS:
                chosen = {name.strip().casefold() for name in selection.get(kind, [])}
                result[kind] = [card for card in production["cards"][kind]
                                if card["name"].strip().casefold() in chosen and
                                (kind != "characters" or
                                 (card["id"] in visually_required | speaker_cards and
                                  not character_is_embedded_form(card, text)))]
            # Local planning is advisory, while visible physical staging is
            # authoritative. Recover a named character that the planner left
            # out when the character is explicitly placed in the action or in
            # a visible cast-timeline bucket. Keep declared off-screen voices
            # off-screen (for example "Rick's voice crackles over comms").
            active_ids = {card["id"] for card in result["characters"]}
            for card in production["cards"]["characters"]:
                if card["id"] in active_ids or character_is_embedded_form(card, text):
                    continue
                if card["id"] in visually_required | speaker_cards:
                    result["characters"].append(card)
                    active_ids.add(card["id"])
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
                      card["id"] in visually_required | speaker_cards and
                      not character_is_embedded_form(card, text)]
        else:
            active = [card for card in characters if
                      (card["id"] in visually_required | speaker_cards or mentioned(card, "characters")) and
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
        presence_roles = character_presence_roles(production, segment)
        visual_characters = [card for card in relevant["characters"]
                             if presence_roles.get(card["id"]) in ("physical", "display", "imagined")]
        missing_visual_identity_names = []
        character_overview = production["overview_asset_ids"].get("characters")
        if not character_overview:
            missing_visual_identity_names = [card["name"] for card in visual_characters
                                             if not card.get("asset_ids")]
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
            candidates = list(visual_characters if kind == "characters" else relevant[kind])
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
                asset_id = (self._ensure_character_identity(production, card)
                            if kind == "characters" else card["asset_ids"][0])
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
        role_subject_ids = {
            role: [subject_for_card[card["id"]] for card in relevant["characters"]
                   if presence_roles.get(card["id"]) == role]
            for role in ("physical", "display", "imagined", "offscreen")
        }
        return {"subject_ids": active_subject_ids,
                "physical_subject_ids": role_subject_ids["physical"],
                "display_subject_ids": role_subject_ids["display"],
                "imagined_subject_ids": role_subject_ids["imagined"],
                "offscreen_subject_ids": role_subject_ids["offscreen"],
                "presence_roles_by_name": {
                    card["name"].strip().casefold(): presence_roles.get(card["id"], "absent")
                    for card in relevant["characters"]},
                "image_asset_ids": selected_images,
                "audio_asset_ids": selected_audio, "overview_kinds": overview_kinds,
                "overview_sources": overview_sources,
                "missing_visual_identity_names": missing_visual_identity_names,
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
        self._rebase_unrelated_card_contexts(production, kind, card)
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
        self._rebase_unrelated_card_contexts(production, kind, card)
        return {"production": self.save(production), "asset": asset}

    def _rebase_unrelated_card_contexts(self, production, kind, card):
        """Keep an edited card local to clips that actually select it.

        The legacy context hash contains the complete library, so adding one
        character image would otherwise mark every prepared clip stale. Rebase
        unrelated clips to the new library hash and leave selected clips on
        their prior hash so validation asks to rebuild only those clips.
        """
        current_hash = production_context_hash(production)
        keys = {str(card.get("id", "")).strip().casefold(),
                str(card.get("name", "")).strip().casefold()}
        keys.discard("")
        for segment in production.get("segments", []):
            if not segment.get("project_id"):
                continue
            selected = {
                str(value).strip().casefold()
                for value in segment.get("card_selection", {}).get(kind, [])
                if str(value).strip()
            }
            if not keys.intersection(selected):
                segment["context_hash"] = current_hash

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
        # A prompt revision changes the render contract. Keep every old take
        # in history, but release a manual pin so an earlier video cannot be
        # silently assembled as though it matched the new prompt.
        segment["selected_video_run_id"] = None
        if project_id:
            # Materialisation can repair an unsafe planner timeline (for
            # example, a remote caller incorrectly staged as a second body in
            # the local room).  Persist that effective physical/off-screen
            # timeline when the rebuilt prompt is accepted.  Otherwise
            # validation immediately diagnoses the old planner timeline again
            # and flips the freshly rebuilt clip back to ``stale``.
            segment["cast_timeline"] = effective_cast_timeline(production, segment)
            segment["cast_timeline_version"] = CAST_TIMELINE_VERSION
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

    def apply_quality_repair(self, ident, segment_id, direction):
        """Apply one explicitly approved repair while preserving all take history.

        This method is deliberately not an automatic recovery hook.  The API
        calls it only after a human has reviewed the failed take and approved
        its bounded repair direction.
        """
        production = self.get(ident)
        segment = next((s for s in production["segments"] if s["id"] == safe_id(segment_id)), None)
        if not segment:
            raise ValueError("Production clip not found.")
        direction = _text(direction, "post-render repair direction", 2000)
        if not direction:
            raise ValueError("The quality review did not provide a safe repair direction.")
        count = int(segment.get("quality_repair_count") or 0)
        if count >= 3:
            raise ValueError(
                "This clip has already used three approved quality-repair attempts. "
                "Review the storyboard and edit its prompt manually before rendering another take.")
        segment["quality_repair_direction"] = direction
        segment["quality_repair_count"] = count + 1
        segment["video_prompt"] = ""
        segment["video_prompt_source"] = ""
        segment["selected_video_run_id"] = None
        segment["status"] = "stale"
        segment["stale_reasons"] = ["视频成片质检发现明确问题，需要按质检要求重建本段"]
        return self.save(production)

    def apply_boundary_action(self, ident, previous_segment_id, segment_id, run_id,
                              action, asset_id=None, can_continue=False):
        production = self.get(ident)
        previous = next((row for row in production["segments"]
                         if row["id"] == safe_id(previous_segment_id)), None)
        segment = next((row for row in production["segments"]
                        if row["id"] == safe_id(segment_id)), None)
        if not previous or not segment or segment["index"] != previous["index"] + 1:
            raise ValueError("Choose two adjacent production clips.")
        if action not in ("save", "use_next", "hard_cut"):
            raise ValueError("Choose save, use_next or hard_cut for this boundary.")
        if asset_id:
            previous["ending_continuity_asset_id"] = safe_id(asset_id)
        if action == "use_next":
            if not can_continue:
                raise ValueError(
                    "This take has no verified MMH3 continuation state. Save the frame for review, or rerender the preceding clip with automatic continuation enabled.")
            production["auto_continue_previous"] = True
            previous["selected_video_run_id"] = safe_id(run_id)
            segment["continue_previous"] = True
            segment["transition_mode"] = "continuous"
            segment["continuity_state"]["mmh3_eligible"] = True
            segment["shot_contract"]["relation_previous"] = "continuous"
        elif action == "hard_cut":
            segment["continue_previous"] = False
            segment["transition_mode"] = "hard_cut"
            segment["continuity_state"]["mmh3_eligible"] = False
            segment["shot_contract"]["relation_previous"] = "hard_cut"
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

    def update_automation(self, ident, changes):
        """Atomically persist background orchestration progress."""
        if not isinstance(changes, dict):
            raise ValueError("Automation update must be an object.")
        allowed = set(normalise_automation(None))
        if set(changes) - allowed:
            raise ValueError("Automation update contains unsupported fields.")
        with self.lock:
            production = self.get(ident)
            automation = copy.deepcopy(production.get("automation", {}))
            automation.update(copy.deepcopy(changes))
            production["automation"] = normalise_automation(automation)
            return self.save(production)

    def assert_storyboard_contract_current(self, production, segment):
        """Block prompt/video work through one unified shot preflight gate."""
        preflight_errors = [row.get("message", "") for row in segment.get("preflight_issues", [])
                            if row.get("severity") == "error"]
        if preflight_errors:
            raise ValueError(
                f"Clip {segment.get('index')} failed shot preflight. Fix or replan it before generating video: " +
                " ".join(preflight_errors[:5]))
        return True

    def _repair_recoverable_preflight(self, production, segment):
        """Downgrade an unsafe adjacent continuation to a normal hard cut.

        Cast, dialogue, source coverage and shot content are deliberately left
        untouched.  Only the editorial boundary and MMH3 continuation switch
        are changed.  All other preflight errors remain blocking after the
        audit is rebuilt, so this cannot conceal missing dialogue, internal
        cuts or an overloaded/unfilmable shot.
        """
        errors = [row for row in segment.get("preflight_issues", [])
                  if row.get("severity") == "error"]
        codes = {str(row.get("code") or "") for row in errors}
        repaired_codes = sorted(codes & AUTO_HARD_CUT_CONTINUITY_CODES)
        changed = False

        if "cut_relation_conflict" in codes:
            segment.setdefault("shot_contract", {})["relation_previous"] = (
                segment.get("transition_mode") or "hard_cut")
            changed = True

        if repaired_codes and (segment.get("transition_mode") == "continuous" or
                               segment.get("continue_previous")):
            segment["transition_mode"] = "hard_cut"
            segment["continue_previous"] = False
            segment.setdefault("continuity_state", {})["mmh3_eligible"] = False
            segment.setdefault("shot_contract", {})["relation_previous"] = "hard_cut"
            warning = (
                "Auto-repaired an unsafe continuous boundary as a normal hard cut; "
                "story, dialogue and cast were preserved (" + ", ".join(repaired_codes) + ").")
            segment["continuity_warnings"] = list(dict.fromkeys(
                [*segment.get("continuity_warnings", []), warning]))[:16]
            changed = True

        if changed:
            # Rebuild all derived reports in memory. materialise() will persist
            # the repaired boundary together with the fresh clip project, so a
            # harmless cut change does not mark every later adopted take stale.
            audit_storyboard_contract(production, backfill_legacy=True)
        return changed

    def assert_video_project_current(self, production, project):
        """Reject stale or visually ungrounded production snapshots.

        A browser can keep an old one-click queue in memory across a server
        restart. Admission must therefore be enforced by the server, not only
        by disabled UI buttons. Standalone Studio projects are unaffected.
        """
        link = project.get("production_link")
        if not isinstance(link, dict) or link.get("production_id") != production.get("id"):
            return
        segment_id = link.get("segment_id")
        segment = next((item for item in production.get("segments", [])
                        if item.get("id") == segment_id), None)
        if segment is None:
            raise ValueError("This production clip no longer exists. Rebuild its video prompt before rendering.")
        self.assert_storyboard_contract_current(production, segment)
        strategy = link.get("reference_strategy") if isinstance(link.get("reference_strategy"), dict) else {}
        if (strategy.get("reference_strategy_version", 0) != REFERENCE_STRATEGY_VERSION or
                segment.get("reference_strategy_version", 0) != REFERENCE_STRATEGY_VERSION or
                segment.get("project_id") != project.get("id")):
            raise ValueError(
                "This clip uses an outdated character-reference assignment. Regenerate this clip prompt before generating video.")
        missing = [str(name).strip() for name in strategy.get("missing_visual_identity_names", [])
                   if str(name).strip()]
        if missing:
            raise ValueError(
                "Visible character identity references are missing: " + ", ".join(missing) +
                ". Add or generate those character-card images, then regenerate this clip prompt.")
        if segment.get("status") == "stale":
            raise ValueError(
                "This production clip is stale. Regenerate this clip prompt before generating video.")

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


def storyboard_planning_chunks(story):
    """Bound each local-model storyboard answer to about five clips.

    A 4,500-character part can require roughly ten fully structured segment
    objects. That JSON regularly exceeds the 4,096-token output ceiling used
    by local OpenAI-compatible servers even though its input fits the context
    window. Two thousand source characters keeps ordinary long episodes near
    five segment objects per call while preserving source order and line
    breaks through ``planning_chunks``.
    """
    return planning_chunks(story, limit=2000)


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


def planning_payload(production, story=None, chunk_index=1, chunk_total=1, previous_ending="",
                     previous_contract=None):
    source_story = story or production["brief"]
    timing = episode_timing_targets(production, chunk_index, chunk_total, source_story)
    locked_dialogue = locked_timed_dialogue(production, source_story)
    source_manifest = source_manifest_for_text(source_story, chunk_index)
    return json.dumps({
        "title": production["title"], "story_or_script": source_story,
        "project_output_language": PRODUCTION_LANGUAGES[production["language"]],
        "current_episode": production.get("current_episode", 1),
        "part": {"index": chunk_index, "total": chunk_total,
                 "previous_part_ending": previous_ending,
                 "previous_part_handoff": previous_contract or {}},
        "episode_timing": timing,
        "locked_source_dialogue": locked_dialogue,
        "source_manifest": source_manifest,
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
                    "named identities per clip. Bind every source_manifest paragraph, dialogue and event ID exactly once and in "
                    "order through source_refs; scene IDs may repeat for adjacent clips. Fill cast_timeline for every clip: "
                    "visible_start and visible_end are exact boundary "
                    "states; enters and exits are physical changes during this clip; offscreen and mentioned_only must never be "
                    "depicted. A character who exited stays absent from later clips until a later cast_timeline.enters explicitly "
                    "brings that character back. Fill continuity_state with concrete opening/ending state, physical screen positions, "
                    "facing and movement directions, eyeline targets, important prop holders plus their visible state/orientation "
                    "(especially phone screen direction and hand), and a truthful mmh3_eligible decision. "
                    "Fill shot_contract with editorial role, shot size, opening/ending composition, axis, edit motivation and explicit "
                    "preserve/change instructions. Set transition_mode to continuous "
                    "only for truly unbroken time/place/action; use "
                    "hard_cut, matched_cut, time_jump, state_change or insert for discontinuities. Make card_selection.characters "
                    "the exact union of visible_start, visible_end, enters and exits. In each locked_source_dialogue group, source_dialogue is the "
                    "complete authored line roster. When requires_translation is false, copy dialogue verbatim. When it is true, translate each "
                    "source_dialogue text once into project_output_language while preserving speaker, count and order; never consolidate, omit, "
                    "reassign or invent a line. For each clip copy only "
                    "exact available card names into card_selection. When previous_part_handoff is present, treat its ending cast, "
                    "positions, facing, motion, eyelines, prop holders, shot size, composition and axis as the authoritative prior "
                    "frame. State explicitly what the first new clip preserves and what it changes; never replay the prior action. "
                    "Start continuously only when that handoff is physically compatible; otherwise author a motivated cut.")
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
