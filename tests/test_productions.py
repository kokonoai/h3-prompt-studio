import copy
import io
import uuid

import pytest
from PIL import Image

from backend.compiler import compile_project
from backend.productions import (REFERENCE_STRATEGY_VERSION, ProductionManager, authored_internal_timing,
                                 card_plan_source_hash, character_aliases, current_episode_story,
                                 episode_timing_targets, fallback_episodes, fallback_segments,
                                 fit_planned_durations, has_substantive_card_library,
                                 locked_timed_dialogue, planning_payload,
                                 production_schema_for_story, render_character_identity,
                                 scoped_character_bible, script_dialogue, timed_clip_groups)
from backend.projects import merge_plan, new_project, shot
from backend.video_workflows import VideoWorkflowManager


def _id():
    return str(uuid.uuid4())


def _rig(tmp_path):
    projects = {}
    assets = {}
    data = tmp_path / "data"

    def load_project(ident):
        return copy.deepcopy(projects[ident])

    def save_project(project):
        projects[project["id"]] = copy.deepcopy(project)
        return project

    def load_asset(ident):
        return copy.deepcopy(assets[ident])

    def store_asset(raw, name, mime):
        ident = _id()
        media_type = "audio" if mime.startswith("audio/") else "image"
        filename = name
        folder = data / "assets" / ident
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(raw)
        value = {"id": ident, "name": name, "filename": filename, "mime": mime,
                 "media_type": media_type, "role": "reference_audio" if media_type == "audio" else "reference_image",
                 "semantic_role": "other", "enabled": False, "locked_order": False,
                 "description": "", "observation": "", "approved_observation": ""}
        assets[ident] = value
        return copy.deepcopy(value)

    source = new_project()
    source["story"]["text"] = "Four friends meet in the library. A says hello."
    save_project(source)
    manager = ProductionManager(data, load_project, save_project, load_asset, store_asset)
    return manager, source, projects, assets, store_asset


def _image(store_asset, name, color):
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, "PNG")
    return store_asset(buffer.getvalue(), name + ".png", "image/png")


def _card(name, asset_ids=(), **extra):
    return {"id": _id(), "name": name, "description": name + " stable traits", "notes": "",
            "asset_ids": list(asset_ids), "locked": True, **extra}


def _planned_clip(title, characters, timeline, transition="hard_cut"):
    return {
        "title": title,
        "story": title,
        "setting": "A clockwork hall",
        "action": title,
        "ending": title + " ends",
        "duration": 8,
        "duration_reason": "One visible action and a readable reaction.",
        "dialogue": [],
        "image_prompt": title,
        "cast_timeline": timeline,
        "transition_mode": transition,
        "card_selection": {"characters": list(characters), "wardrobe": [], "props": [],
                           "environments": [], "voices": []},
    }


def test_generated_card_image_is_project_scoped_and_newest_is_primary(tmp_path):
    manager, source, projects, _, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "title": "Episode", "brief": "A character enters."})
    old = _image(store_asset, "old", "red")
    card = _card("Hero", [old["id"]])
    production["cards"]["characters"] = [card]
    manager.save(production)
    original_source = copy.deepcopy(projects[source["id"]])
    generated = _image(store_asset, "new", "blue")

    result = manager.attach_generated_card_asset(production["id"], "characters", card["id"], generated)

    assert result["production"]["cards"]["characters"][0]["asset_ids"] == [generated["id"], old["id"]]
    assert projects[source["id"]] == original_source
    assert manager.get(production["id"])["cards"]["characters"][0]["asset_ids"][0] == generated["id"]


def test_local_ai_selects_exact_project_cards_uses_dense_cast_overview_and_applies_language(tmp_path):
    manager, source, projects, assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "title": "Episode", "brief": source["story"]["text"],
                                 "language": "ja"})
    characters = [_card(name, [_image(store_asset, name, color)["id"]]) for name, color in
                  (("A", "red"), ("B", "green"), ("C", "blue"), ("D", "yellow"))]
    environment = _card("Library", [_image(store_asset, "library", "gray")["id"]])
    voice_asset = store_asset(b"voice", "a.wav", "audio/wav")
    voice = _card("A voice", [voice_asset["id"]], character_card_id=characters[0]["id"],
                  voice_id="A_JA", language="ja", pace="calm")
    production["cards"].update({"characters": characters, "environments": [environment],
                                "voices": [voice], "styles": [_card("Anime ink") ]})
    manager.save(production)

    selected = {"characters": ["a", "B", "C", "D", "Not in library"], "wardrobe": [],
                "props": [], "environments": ["library"], "voices": ["A voice"]}
    planned = [{"title": "出会い", "story": "四人が図書館で会う。", "setting": "図書館",
                "action": "四人が集まり、Aが挨拶する。", "ending": "全員が立ち止まる。",
                "duration": 8, "duration_reason": "会話と反応に8秒。",
                "dialogue": [{"speaker": "A", "text": "こんにちは。", "language": "Japanese", "voiceover": False}],
                "image_prompt": "図書館にいる四人", "card_selection": selected}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    segment = production["segments"][0]

    assert segment["card_selection_source"] == "local_ai"
    assert segment["card_selection"]["characters"] == ["A", "B", "C", "D"]
    assert segment["card_selection"]["environments"] == ["Library"]
    payload = planning_payload(production)
    assert '"project_output_language": "Japanese"' in payload
    assert '"name": "A voice"' in payload

    result = manager.materialise(production["id"], segment["id"])
    project = result["project"]
    strategy = project["production_link"]["reference_strategy"]
    assert project["production_language"] == "ja"
    assert project["shots"][0]["dialogue"][0]["language"] == "Japanese"
    assert "voice target language Japanese" in project["shots"][0]["dialogue"][0]["delivery"]
    assert strategy["overview_kinds"] == ["characters"]
    assert strategy["overview_sources"] == {"characters": "automatic"}
    assert len(strategy["image_asset_ids"]) == 2
    assert strategy["audio_asset_ids"] == [voice_asset["id"]]
    assert strategy["voice_authority"] == "audio_primary"
    voice_reference = next(asset for asset in project["assets"] if asset["id"] == voice_asset["id"])
    assert "PRIMARY VOICE IDENTITY AUTHORITY" in voice_reference["description"]
    character_overview = next(asset for asset in project["assets"]
                              if asset.get("reference_card_kind") == "characters")
    assert character_overview["reference_overview"] is True
    assert character_overview["reference_card_names"] == ["A", "B", "C", "D"]
    compiled = compile_project(project)
    assert compiled["valid"] is True
    assert "primary authority for that speaker's audible identity" in compiled["prompt"]
    assert "primary_voice_reference" in compiled["prompt"]
    assert len([asset for asset in project["assets"] if asset["role"] == "reference_image"]) <= 9


def test_project_language_validation_and_legacy_default(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A scene."})
    assert production["language"] == "zh-CN"
    production.pop("language")
    assert manager.validate(production)["language"] == "zh-CN"


def test_episode_target_accepts_half_a_minute_and_rejects_shorter_values(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A short scene.",
                                 "episode_minutes": 0.5})
    assert production["episode_minutes"] == 0.5
    production["episode_minutes"] = 0.49
    with pytest.raises(ValueError, match="0.5-180"):
        manager.validate(production)


def test_inline_shot_timecodes_ignore_series_preamble_and_section_ranges():
    screenplay = """Episode Four
Cast: A, B, C, D.
Production note: keep every recurring identity available to the episode.
Part 3
02:00—03:00｜60 seconds
分镜15｜02:00—02:08
A crosses the fixed walkway. B answers.
分镜16｜02:08—02:16
B closes the gate while A waits.
"""

    groups = timed_clip_groups(screenplay)
    assert [(group["start"], group["end"]) for group in groups] == [(120, 128), (128, 136)]
    assert "Cast:" not in groups[0]["text"]
    assert groups[0]["text"].startswith("A crosses")
    clips = fallback_segments(screenplay, 16, "en")
    assert len(clips) == 2
    assert all("Production note" not in clip["story"] for clip in clips)


def test_thirty_second_episode_targets_about_three_five_to_fifteen_second_clips(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({
        "source_project": source,
        "brief": "She wakes. She crosses the room. She opens the letter.",
        "episode_minutes": 0.5,
    })
    timing = episode_timing_targets(production)
    assert timing == {
        "episode_target_seconds": 30,
        "part_target_seconds": 30,
        "default_clip_seconds": 10,
        "recommended_clip_count": 3,
        "feasible_clip_count": {"minimum": 2, "maximum": 6},
        "clip_duration_seconds": {"minimum": 5, "maximum": 15},
        "source_timed_beat_count": 0,
        "timed_clip_groups": [],
    }
    payload = planning_payload(production)
    assert '"episode_target_seconds": 30' in payload
    assert '"recommended_clip_count": 3' in payload
    clips = fallback_segments(production["brief"], 30, "en")
    assert len(clips) == 3
    assert sum(clip["duration"] for clip in clips) == 30
    assert all(5 <= clip["duration"] <= 15 for clip in clips)


def test_merged_authored_clip_gets_local_action_timing_without_dialogue_copy():
    production = {
        "episode_count": 1,
        "brief": """0:00-0:05
A opens the door.
A: “First exact line.”

0:05-0:10
A crosses the room.
A: “Second exact line.”

0:10-0:14
A reaches the desk.
A: “Third exact line.”""",
        "cards": {"characters": [{"name": "A"}]},
    }
    second_group = timed_clip_groups(production["brief"])[1]
    timing = authored_internal_timing(production, {"index": 2, "duration": 9,
                                                   "story": second_group["text"]})

    assert "0.00-5.00s" in timing
    assert "5.00-9.00s" in timing
    assert "A crosses the room." in timing
    assert "A reaches the desk." in timing
    assert "First exact line" not in timing
    assert "Second exact line" not in timing
    assert "Third exact line" not in timing


def test_authored_timing_is_not_attached_to_unrelated_storyboard_clip():
    production = {"episode_count": 1,
                  "brief": "0:00-0:05\nYuki sits in the living room.\n\n0:05-0:10\nNox lies on the sofa.",
                  "cards": {"characters": [{"name": "Yuki"}, {"name": "Nox"}]}}
    clip = {"index": 1, "duration": 10, "story": "Pokke visits the clock tower.",
            "action": "Pokke examines a book."}
    assert authored_internal_timing(production, clip) == ""


def test_scoped_character_bible_uses_complete_selected_sections_only():
    production = {"character_bible": """### Mimi
Soft pear-shaped friend.

### Courage Star
Transforms into Mimi's badge.

### Chappi
Small yellow bird."""}
    selected = [{"name": "Mimi"}, {"name": "Chappi"}]

    context = scoped_character_bible(production, selected)

    assert "Soft pear-shaped friend." in context
    assert "Small yellow bird." in context
    assert "Courage Star" not in context


def test_single_episode_timecodes_drive_clip_groups_and_preserve_exact_dialogue(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    screenplay = """Title | 30 seconds

0:00-0:05
Yuki looks at her phone and turns it face-down.

0:05-0:10
Pokke:
“Okay! Laundry first. Then dishes, groceries, emails—”
Pokke continues:
“And you haven’t eaten breakfast.”

0:10-0:14
Pokke:
“HEY!”
Nox:
“She’s tired.”

0:14-0:21
The friends quietly gather around Yuki.

0:21-0:26
Yuki:
“So… we’re doing nothing today?”

0:26-0:30
Nox:
“No.”
“We’re very busy doing nothing.”
Pokke pops out of the cushions:
“Should I put that on the schedule?”
All:
“No.”"""
    production = manager.create({"source_project": source, "brief": screenplay,
                                 "episode_minutes": .5, "language": "en"})
    characters = [_card(name) for name in ("Yuki", "Pokke", "Nox")]
    production["cards"]["characters"] = characters
    production["cards"]["voices"] = [
        _card(name, character_card_id=character["id"])
        for name, character in zip(("Yuki voice", "Pokke voice", "Nox voice"), characters)
    ]
    production["episodes"] = [{
        "id": _id(), "index": 1, "title": "Paraphrased", "logline": "Changed",
        "story": "An AI synopsis that omitted every exact line.",
        "character_card_ids": [card["id"] for card in characters],
        "returning_character_card_ids": [], "continuity_notes": "",
    }]
    production = manager.save(production)

    assert current_episode_story(production) == screenplay
    groups = timed_clip_groups(screenplay)
    assert [(item["start"], item["end"], item["duration"]) for item in groups] == [
        (0, 5, 5), (5, 14, 9), (14, 21, 7), (21, 30, 9)]
    timing = episode_timing_targets(production)
    assert timing["source_timed_beat_count"] == 6
    assert timing["recommended_clip_count"] == 4
    assert [item["duration_seconds"] for item in timing["timed_clip_groups"]] == [5, 9, 7, 9]
    exact_schema = production_schema_for_story(screenplay)["properties"]["segments"]
    assert exact_schema["minItems"] == exact_schema["maxItems"] == 4

    locked = locked_timed_dialogue(production)
    assert [line["text"] for line in locked[1]["dialogue"]] == [
        "Okay! Laundry first. Then dishes, groceries, emails—",
        "And you haven’t eaten breakfast.", "HEY!", "She’s tired."]
    assert [line["text"] for line in locked[3]["dialogue"]] == [
        "So… we’re doing nothing today?", "No.", "We’re very busy doing nothing.",
        "Should I put that on the schedule?", "No."]
    assert locked[3]["dialogue"][-2]["speaker"] == "Pokke"

    planned = [{
        "title": f"Clip {index + 1}", "story": group["text"], "setting": "room",
        "action": group["text"], "ending": "hold", "duration": 10,
        "duration_reason": "model estimate",
        "dialogue": [{"speaker": "Pokke", "text": "Paraphrased.",
                      "language": "English", "voiceover": False}],
        "image_prompt": group["text"],
        "card_selection": {kind: [] for kind in ("characters", "wardrobe", "props", "environments", "voices")},
    } for index, group in enumerate(groups)]
    planned = fit_planned_durations(planned, 30, "en", screenplay)
    assert [item["duration"] for item in planned] == [5, 9, 7, 9]
    result = manager.apply_plan(production["id"], planned, "local_ai")
    assert result["segments"][0]["dialogue"] == []
    assert [line["text"] for line in result["segments"][1]["dialogue"]] == [
        "Okay! Laundry first. Then dishes, groceries, emails—",
        "And you haven’t eaten breakfast.", "HEY!", "She’s tired."]
    assert result["segments"][1]["card_selection"]["characters"] == ["Pokke", "Nox"]
    assert result["segments"][1]["card_selection"]["voices"] == ["Pokke voice", "Nox voice"]

    fallback = manager.apply_plan(production["id"], planned, "local_heuristic")
    assert fallback["segments"][1]["card_selection_source"] == "heuristic"
    assert fallback["segments"][1]["card_selection"]["characters"] == ["Pokke", "Nox"]
    assert fallback["segments"][1]["card_selection"]["voices"] == ["Pokke voice", "Nox voice"]


def test_dense_timed_group_can_split_without_losing_locked_dialogue(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    screenplay = """0:00-0:10
Pokke crosses the platform while Bokka holds position.
Pokke:
“Keep your side clear.”"""
    production = manager.create({"source_project": source, "brief": screenplay,
                                 "episode_minutes": .5, "language": "en"})
    production["cards"]["characters"] = [
        _card("Pokke", description="Rectangular backpack with zipper mouth."),
        _card("Bokka", description="Round striped watermelon with coral bow."),
    ]
    production = manager.save(production)
    schema = production_schema_for_story(screenplay)["properties"]["segments"]
    assert schema["minItems"] == 1
    assert schema["maxItems"] == 2
    empty_cards = {kind: [] for kind in ("characters", "wardrobe", "props", "environments", "voices")}
    planned = [
        {"title": "Approach", "story": "Pokke crosses the platform.", "setting": "platform",
         "action": "Pokke crosses the platform.", "ending": "Pokke reaches the rail.",
         "duration": 5, "duration_reason": "first causal action", "dialogue": [],
         "image_prompt": "Pokke alone on the platform.",
         "card_selection": {**empty_cards, "characters": ["Pokke"]}},
        {"title": "Answer", "story": "Bokka holds position.", "setting": "platform",
         "action": "Bokka holds position while Pokke speaks.", "ending": "Both hold separate positions.",
         "duration": 5, "duration_reason": "second causal action",
         "dialogue": [{"speaker": "Pokke", "text": "Keep your side clear.",
                       "language": "English", "voiceover": False}],
         "image_prompt": "Pokke and Bokka remain visibly distinct.",
         "card_selection": {**empty_cards, "characters": ["Pokke", "Bokka"]}},
    ]
    fitted = fit_planned_durations(planned, 10, "en", screenplay)
    assert [item["duration"] for item in fitted] == [5, 5]
    result = manager.apply_plan(production["id"], fitted, "local_ai")
    assert len(result["segments"]) == 2
    assert result["segments"][1]["dialogue"][0]["text"] == "Keep your side clear."

    broken = copy.deepcopy(fitted)
    broken[1]["dialogue"][0]["text"] = "Paraphrased."
    with pytest.raises(ValueError, match="omitted, changed, reassigned or reordered"):
        manager.apply_plan(production["id"], broken, "local_ai")


def test_markdown_speaker_cues_keep_exact_dialogue_and_strip_acting_notes(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    screenplay = """### 分镜66｜0:00—0:10
**Pokke｜声音不大，这次没有绕弯：**
> “I knew it was stuck. I let you try anyway. I’m sorry.”

### 分镜67｜0:10—0:20
__Mimi（认真，但没有生气）：__ “Don't make us guess again.”"""
    assert script_dialogue(screenplay) == [
        {"speaker": "Pokke", "text": "I knew it was stuck. I let you try anyway. I’m sorry."},
        {"speaker": "Mimi", "text": "Don't make us guess again."},
    ]
    production = manager.create({"source_project": source, "brief": screenplay,
                                 "episode_minutes": .5, "language": "en"})
    production["cards"]["characters"] = [_card("Pokke"), _card("Mimi")]
    production = manager.save(production)
    locked = locked_timed_dialogue(production)
    assert [group["dialogue_parse_failed"] for group in locked] == [False, False]
    assert locked[0]["dialogue"][0]["speaker"] == "Pokke"
    assert locked[1]["dialogue"][0]["speaker"] == "Mimi"

    empty_cards = {kind: [] for kind in
                   ("characters", "wardrobe", "props", "environments", "voices")}
    planned = [{
        "title": f"Clip {index + 1}", "story": group["text"], "setting": "clock tower",
        "action": group["text"], "ending": "hold", "duration": 10,
        "duration_reason": "authored timing",
        "dialogue": [{"speaker": "Wrong", "text": "Paraphrased.",
                      "language": "English", "voiceover": False}],
        "image_prompt": group["text"], "card_selection": empty_cards,
    } for index, group in enumerate(timed_clip_groups(screenplay))]
    result = manager.apply_plan(production["id"], planned, "local_ai")
    assert result["segments"][0]["dialogue"] == locked[0]["dialogue"]
    assert result["segments"][1]["dialogue"] == locked[1]["dialogue"]


def test_translation_plan_cannot_be_cleared_by_empty_exact_language_lock(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    screenplay = """0:00-0:10
**Mimi｜轻声：**
> “你终于来了。”"""
    production = manager.create({"source_project": source, "brief": screenplay,
                                 "episode_minutes": .5, "language": "en"})
    production["cards"]["characters"] = [_card("Mimi")]
    production = manager.save(production)
    locked = locked_timed_dialogue(production)[0]
    assert locked["dialogue"] == []
    assert locked["requires_translation"] is True
    assert locked["source_dialogue"][0]["text"] == "你终于来了。"

    planned = [{
        "title": "Arrival", "story": screenplay, "setting": "station",
        "action": "Mimi looks up.", "ending": "Mimi holds her gaze.", "duration": 10,
        "duration_reason": "authored timing",
        "dialogue": [{"speaker": "Mimi", "text": "You finally came.",
                      "language": "English", "voiceover": False}],
        "image_prompt": "Mimi at the station", "card_selection": {},
    }]
    result = manager.apply_plan(production["id"], planned, "local_ai")
    assert result["segments"][0]["dialogue"][0]["text"] == "You finally came."


def test_suspected_dialogue_parse_failure_stops_before_silent_overwrite(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    screenplay = """0:00-0:10
**Pokke：**
> “”"""
    production = manager.create({"source_project": source, "brief": screenplay,
                                 "episode_minutes": .5, "language": "en"})
    production = manager.save(production)
    assert locked_timed_dialogue(production)[0]["dialogue_parse_failed"] is True
    planned = [{
        "title": "Silent", "story": screenplay, "setting": "room",
        "action": "Pokke waits.", "ending": "Pokke waits.", "duration": 10,
        "duration_reason": "authored timing", "dialogue": [],
        "image_prompt": "Pokke waits", "card_selection": {},
    }]
    with pytest.raises(ValueError, match="could not be parsed"):
        manager.apply_plan(production["id"], planned, "local_ai")


def test_episode_cast_tracks_returning_characters_and_current_story(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A meets B. B returns later.",
                                 "episode_count": 2, "episode_minutes": 6})
    a, b = _card("A"), _card("B")
    production["cards"]["characters"] = [a, b]
    manager.save(production)
    planned = [
        {"title": "Meeting", "logline": "A meets B", "story": "A meets B.",
         "character_names": ["A", "B"], "continuity_notes": "B keeps the key."},
        {"title": "Return", "logline": "B returns", "story": "B returns later.",
         "character_names": ["B"], "continuity_notes": "The key returns."},
    ]
    production = manager.apply_episode_plan(production["id"], planned, "local_ai")
    assert production["episodes"][0]["returning_character_card_ids"] == []
    assert production["episodes"][1]["returning_character_card_ids"] == [b["id"]]
    production["current_episode"] = 2
    production = manager.save(production)
    assert current_episode_story(production) == "B returns later."


def test_named_card_collection_merges_without_overwriting_project_cards(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    first = manager.create({"source_project": source, "brief": "A scene.", "language": "en"})
    first["series_voice_style"] = "Neutral American English; clean close studio dialogue."
    hero = _card("Shared hero")
    first["cards"]["characters"] = [hero]
    first["cards"]["voices"] = [_card(
        "Shared hero voice", character_card_id=hero["id"], voice_id="HERO_V1",
        language="en", pace="warm and measured",
    )]
    overview = _image(store_asset, "shared-cast-overview", "black")
    first["overview_asset_ids"]["characters"] = overview["id"]
    first = manager.save(first)
    collection = manager.save_card_collection(first["id"], "Library arc")
    second = manager.create({"source_project": source, "brief": "Another scene.", "language": "ja"})
    second["cards"]["characters"] = [_card("Local guest")]
    second = manager.save(second)
    merged = manager.apply_card_collection(second["id"], collection["id"])
    assert [card["name"] for card in merged["cards"]["characters"]] == ["Local guest", "Shared hero"]
    assert merged["card_collection_name"] == "Library arc"
    assert merged["overview_asset_ids"]["characters"] == overview["id"]
    assert merged["series_voice_style"] == first["series_voice_style"]
    assert merged["cards"]["voices"][0]["voice_id"] == "HERO_V1"
    assert merged["cards"]["voices"][0]["pace"] == "warm and measured"
    assert merged["cards"]["voices"][0]["language"] == "ja"
    assert manager.get_card_collection(collection["id"])["cards"]["voices"][0]["language"] == "en"


def test_shared_card_set_sync_preserves_other_parts_and_completes_placeholders(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    first = manager.create({"source_project": source, "brief": "Part one."})
    portrait = _image(store_asset, "hero", "teal")
    hero = _card("Shared hero", [portrait["id"]], image_generation_prompt="Full-body front view on a plain backdrop")
    first["cards"]["characters"] = [hero]
    first = manager.save(first)
    collection = manager.save_card_collection(first["id"], "One story")

    second = manager.create({"source_project": source, "brief": "Part two."})
    placeholder = _card("Shared hero")
    placeholder["description"] = ""
    prop = _card("Recurring key")
    second["cards"]["characters"] = [placeholder]
    second["cards"]["props"] = [prop]
    second = manager.save(second)
    second = manager.apply_card_collection(second["id"], collection["id"])
    assert second["cards"]["characters"][0]["id"] == placeholder["id"]
    assert second["cards"]["characters"][0]["description"] == hero["description"]
    assert second["cards"]["characters"][0]["image_generation_prompt"] == hero["image_generation_prompt"]
    assert portrait["id"] in second["cards"]["characters"][0]["asset_ids"]

    manager.save_card_collection(second["id"], "One story")
    shared = manager.get_card_collection(collection["id"])
    assert {card["name"] for card in shared["cards"]["characters"]} == {"Shared hero"}
    assert portrait["id"] in shared["cards"]["characters"][0]["asset_ids"]
    assert shared["cards"]["characters"][0]["image_generation_prompt"] == hero["image_generation_prompt"]
    assert {card["name"] for card in shared["cards"]["props"]} == {"Recurring key"}
    # A third part can now load the complete set without touching the originals.
    third = manager.create({"source_project": source, "brief": "Part three."})
    third = manager.apply_card_collection(third["id"], collection["id"])
    assert len(third["cards"]["characters"]) == 1
    assert len(third["cards"]["props"]) == 1


def test_auto_keyframe_suggestions_are_sparse_and_default_off(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Two locations."})
    assert production["auto_keyframes_enabled"] is False
    plan = [
        {"title": "Library", "story": "A enters.", "setting": "Library interior", "action": "A enters.",
         "ending": "A stops.", "duration": 8, "dialogue": [], "image_prompt": "Wide library room", "card_selection": {}},
        {"title": "Same room", "story": "A sits.", "setting": "Library interior", "action": "A sits.",
         "ending": "A reads.", "duration": 8, "dialogue": [], "image_prompt": "Library desk", "card_selection": {}},
        {"title": "Outside", "story": "A exits.", "setting": "Rainy street", "action": "A exits.",
         "ending": "A waits.", "duration": 8, "dialogue": [], "image_prompt": "Rainy street corner", "card_selection": {}},
    ]
    production = manager.apply_plan(production["id"], plan, "local_heuristic")
    suggestions = manager.auto_keyframe_suggestions(production["id"])
    assert [item["index"] for item in suggestions] == [1, 3]
    assert all("No people" in item["prompt"] for item in suggestions)
    production["auto_keyframes_enabled"] = True
    production = manager.save(production)
    assert production["auto_keyframes_enabled"] is True


def test_card_set_manager_preserves_projects_and_rebinds_relationships(tmp_path):
    manager, source, _projects, assets, store_asset = _rig(tmp_path)
    original = manager.create({"source_project": source, "brief": "Reusable cast.", "language": "en"})
    portrait = _image(store_asset, "hero-portrait", "teal")
    hero = _card("Shared hero", [portrait["id"]])
    voice = _card("Shared hero voice", character_card_id=hero["id"], voice_id="HERO_V1",
                  language="en", pace="bright")
    hero["voice_card_id"] = voice["id"]
    wardrobe = _card("Blue coat", owner_card_id=hero["id"])
    original["cards"]["characters"] = [hero]
    original["cards"]["wardrobe"] = [wardrobe]
    original["cards"]["voices"] = [voice]
    original = manager.save(original)
    collection = manager.save_card_collection(original["id"], "Reusable heroes")

    target = manager.create({"source_project": source, "brief": "A new story.", "language": "ja"})
    local_hero = _card("Shared hero")
    target["cards"]["characters"] = [local_hero]
    target = manager.save(target)
    merged = manager.apply_card_collection(target["id"], collection["id"])
    assert [card["id"] for card in merged["cards"]["characters"]] == [local_hero["id"]]
    assert merged["cards"]["wardrobe"][0]["owner_card_id"] == local_hero["id"]
    assert merged["cards"]["voices"][0]["character_card_id"] == local_hero["id"]
    assert merged["cards"]["characters"][0]["voice_card_id"] == merged["cards"]["voices"][0]["id"]
    assert merged["cards"]["voices"][0]["language"] == "ja"

    renamed = manager.rename_card_collection(collection["id"], "Hero library")
    assert renamed["name"] == "Hero library"
    summary = manager.list_card_collections()[0]
    assert summary["counts"]["characters"] == 1
    assert summary["counts"]["wardrobe"] == 1
    duplicate = manager.duplicate_card_collection(collection["id"], "Hero library backup")
    assert duplicate["id"] != collection["id"]
    assert duplicate["cards"] == renamed["cards"]

    result = manager.delete_card_collection(collection["id"])
    assert result["archived"] is True
    assert result["project_cards_preserved"] is True
    assert portrait["id"] in assets
    reloaded = manager.get(target["id"])
    assert reloaded["card_collection_id"] is None
    assert reloaded["cards"]["characters"][0]["id"] == local_hero["id"]
    assert reloaded["cards"]["voices"][0]["character_card_id"] == local_hero["id"]
    assert list((tmp_path / "data" / "card_collection_archive").glob(collection["id"] + "-*.json"))
    with pytest.raises(ValueError, match="Card collection not found"):
        manager.get_card_collection(collection["id"])
    assert manager.get_card_collection(duplicate["id"])["name"] == "Hero library backup"


def test_text_only_voice_bible_uses_only_current_speakers(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    series_style = "Neutral American English; expressive animated acting; never babyish or robotic."
    production = manager.create({
        "source_project": source,
        "brief": "A greets B while B remains silent.",
        "language": "en",
        "series_voice_style": series_style,
    })
    a, b = _card("A"), _card("B")
    a_voice_text = "A warm, fast and buoyant animated voice with clear diction."
    b_voice_text = "A low, restrained voice with dry humour."
    a_voice = _card(
        "A voice", character_card_id=a["id"], description=a_voice_text,
        notes="Never robotic.", voice_id="A_EN", language="en", pace="quick",
    )
    b_voice = _card(
        "B voice", character_card_id=b["id"], description=b_voice_text,
        notes="Never growling.", voice_id="B_EN", language="en", pace="measured",
    )
    production["cards"].update({"characters": [a, b], "voices": [a_voice, b_voice]})
    manager.save(production)
    planned = [{
        "title": "Greeting", "story": "A greets B.", "setting": "library",
        "action": "A greets B while B listens.", "ending": "B looks up.",
        "duration": 5, "duration_reason": "one greeting", "image_prompt": "A and B in a library",
        "dialogue": [{"speaker": "A", "text": "Good morning.", "language": "English", "voiceover": False}],
        # The model may over-select a silent voice; speaker binding must win.
        "card_selection": {"characters": ["A", "B"], "voices": ["B voice"]},
    }]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    result = manager.materialise(production["id"], production["segments"][0]["id"])
    project = result["project"]
    prompt_context = project["custom_instructions"]
    strategy = project["production_link"]["reference_strategy"]

    assert series_style in prompt_context
    assert "Project dialogue language: English" in prompt_context
    assert a_voice_text in prompt_context
    assert "Never robotic." in prompt_context
    assert b_voice_text not in prompt_context
    assert "Never growling." not in prompt_context
    assert project["h3_verbatim_blocks"] and project["h3_verbatim_blocks"][0] in prompt_context
    assert strategy["audio_asset_ids"] == []
    assert strategy["voice_authority"] == "text_only"
    assert "No voice audio is uploaded for this clip" in prompt_context
    assert "All other prose is silent production direction" in prompt_context
    assert "these descriptions are never additional words to say" in prompt_context
    assert "Voice continuity supplement: stable identity key A_EN" in prompt_context
    assert "does not replace or rewrite the verbatim card above" in prompt_context
    assert project["shots"][0]["dialogue"][0]["delivery"].startswith("follow locked voice card A voice")


def test_director_version_keeps_voice_metadata_scoped_and_classic_available(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({
        "source_project": source, "brief": "Pokke speaks; Nox stays silent.",
        "language": "en", "prompt_version": "continuity_director",
        "series_voice_style": "Polished animated English dialogue.\n#### The characters should have distinct voices:\nPokke: brisk.\nNox: smoky.",
    })
    pokke = _card("Pokke", [_image(store_asset, "pokke", "green")["id"]])
    nox = _card("Nox", [_image(store_asset, "nox", "indigo")["id"]])
    pokke_voice = _card("Pokke voice", character_card_id=pokke["id"],
                        description="Brisk and bouncy.", voice_id="POKKE", language="en")
    nox_voice = _card("Nox voice", character_card_id=nox["id"],
                      description="Smooth and smoky.", voice_id="NOX", language="en")
    production["cards"].update({"characters": [pokke, nox], "voices": [pokke_voice, nox_voice]})
    manager.save(production)
    production = manager.apply_plan(production["id"], [{
        "title": "Suspicion", "story": "Pokke asks Nox to wait.", "setting": "quiet garden",
        "action": "Pokke looks at Nox.", "ending": "Nox stays in place.",
        "duration": 5, "duration_reason": "one short line", "image_prompt": "Pokke and Nox",
        "dialogue": [{"speaker": "Pokke", "text": "Wait here.", "language": "English", "voiceover": False}],
        "card_selection": {"characters": ["Pokke", "Nox"]},
    }], "local_ai")
    materialised = manager.materialise(production["id"], production["segments"][0]["id"])
    p = materialised["project"]
    assert p["prompt_version"] == "continuity_director"
    assert p["narrative_voice"]["cards"][0]["name"] == "Pokke voice"
    assert len(p["narrative_voice"]["cards"]) == 1
    assert "VOICE DIRECTION" not in p["custom_instructions"]
    assert not p["h3_verbatim_blocks"]
    compiled = compile_project(p)
    assert compiled["valid"], compiled["issues"]
    text = compiled["prompt"]
    assert text.startswith("asset_roles:")
    assert "Brisk and bouncy" in text and "Smooth and smoky" not in text
    assert "Nox: smoky" not in text


def test_materialise_keeps_silent_selected_cast_visible_and_voiceover_offscreen(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A talks while B watches and C narrates."})
    a, b, c = _card("A"), _card("B"), _card("C")
    production["cards"]["characters"] = [a, b, c]
    manager.save(production)
    planned = [{
        "title": "Three roles", "story": "A talks while B watches and C narrates.", "setting": "room",
        "action": "A speaks; B watches quietly.", "ending": "A and B remain in frame.",
        "duration": 5, "duration_reason": "one exchange", "image_prompt": "A and B in a room",
        "dialogue": [
            {"speaker": "A", "text": "Ready.", "language": "English", "voiceover": False},
            {"speaker": "C", "text": "They were not ready.", "language": "English", "voiceover": True},
        ],
        "card_selection": {"characters": ["A", "B", "C"], "voices": []},
    }]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    names = {subject["id"]: subject["name"] for subject in project["subjects"]}
    scene = project["shots"][0]
    assert [names[ident] for ident in scene["visible_subject_ids"]] == ["A", "B"]
    assert [names[ident] for ident in scene["offscreen_subject_ids"]] == ["C"]


def test_materialise_compiles_all_as_visible_ensemble_not_extra_character(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A and B answer together.", "language": "en"})
    a = _card("A", [_image(store_asset, "A", "red")["id"]])
    b = _card("B", [_image(store_asset, "B", "blue")["id"]])
    production["cards"]["characters"] = [a, b]
    manager.save(production)
    planned = [{
        "title": "Together", "story": "A and B answer together.", "setting": "room",
        "action": "A and B look up together.", "ending": "Both remain visible.",
        "duration": 5, "duration_reason": "short chorus", "image_prompt": "A and B",
        "dialogue": [{"speaker": "All", "text": "No.", "language": "English", "voiceover": False}],
        "card_selection": {"characters": ["A", "B"], "voices": []},
    }]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    scene = project["shots"][0]
    names = {subject["id"]: subject["name"] for subject in project["subjects"]}
    assert [names[ident] for ident in scene["visible_subject_ids"]] == ["A", "B"]
    assert scene["offscreen_subject_ids"] == []
    chorus = next(subject for subject in project["subjects"] if subject["name"] == "All")
    assert chorus["collective_member_ids"] == scene["visible_subject_ids"]
    compiled = compile_project(project)
    assert compiled["valid"] is True, compiled["issues"]
    assert "the visible ensemble" in compiled["prompt"]
    assert "say together in exact unison" in compiled["prompt"]
    assert "All is visible" not in compiled["prompt"]


def test_materialise_repairs_shared_legacy_subject_ids_and_isolates_clip_sound(tmp_path):
    manager, source, projects, _assets, store_asset = _rig(tmp_path)
    source["soundscape"] = "Unrelated library footsteps."
    source["music"] = "Unrelated library score."
    source["custom_instructions"] = "Keep the user's master rendering constraint."
    projects[source["id"]] = copy.deepcopy(source)
    production = manager.create({"source_project": source, "brief": "Yuki watches while Pokke speaks.", "language": "en"})
    yuki_image = _image(store_asset, "Yuki", "pink")
    pokke_image = _image(store_asset, "Pokke", "green")
    shared_subject_id = _id()
    yuki = _card("Yuki", [yuki_image["id"]], subject_id=shared_subject_id)
    pokke = _card("Pokke", [pokke_image["id"]], subject_id=shared_subject_id)
    pokke_voice = _card("Pokke voice", character_card_id=pokke["id"], voice_id="POKKE_EN", language="en")
    production["cards"]["characters"] = [yuki, pokke]
    production["cards"]["voices"] = [pokke_voice]
    production = manager.save(production)
    assert [card["subject_id"] for card in production["cards"]["characters"]] == [None, None]
    planned = [{
        "title": "Morning", "story": "Yuki watches while Pokke speaks.", "setting": "living room",
        "action": "Yuki sits on the sofa while Pokke reads a checklist.", "ending": "Both remain visible.",
        "duration": 5, "duration_reason": "one exchange", "image_prompt": "Yuki and Pokke",
        "dialogue": [{"speaker": "Pokke", "text": "Breakfast first.", "language": "English", "voiceover": False}],
        "card_selection": {"characters": ["Yuki", "Pokke"], "voices": ["Pokke voice"]},
    }]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    production["segments"][0].update({
        "video_prompt": "OLD PROMPT THAT NO LONGER MATCHES",
        "video_prompt_source": "local_ai",
        "prompt_seconds": 12.5,
        "prompt_updated_at": 12345,
    })
    production = manager.save(production)
    first = manager.materialise(production["id"], production["segments"][0]["id"])
    project = first["project"]
    rebuilt_segment = first["production"]["segments"][0]
    assert rebuilt_segment["video_prompt"] == ""
    assert rebuilt_segment["video_prompt_source"] == ""
    assert rebuilt_segment["prompt_seconds"] is None
    assert rebuilt_segment["prompt_updated_at"] is None
    mapped = {subject["name"]: subject for subject in project["subjects"] if subject["name"] in {"Yuki", "Pokke"}}
    assert set(mapped) == {"Yuki", "Pokke"}
    assert mapped["Yuki"]["id"] != mapped["Pokke"]["id"]
    assert mapped["Yuki"]["asset_ids"] == [yuki_image["id"]]
    assert mapped["Pokke"]["asset_ids"] == [pokke_image["id"]]
    assert project["soundscape"] == ""
    assert project["music"] == ""
    assert project["shots"][0]["dialogue"][0]["delivery"].startswith("follow locked voice card Pokke voice")
    assert project["production_planning_context"].count("LONG-FORM PRODUCTION CONTEXT") == 1
    assert "LONG-FORM PRODUCTION CONTEXT" not in project["custom_instructions"]
    project["h3_prompt_translation"] = {
        "version": 1, "source_sha256": "old-source", "target_language": "en", "prompt": "old delivery",
    }
    projects[project["id"]] = copy.deepcopy(project)
    second = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    assert second["production_planning_context"].count("LONG-FORM PRODUCTION CONTEXT") == 1
    assert "LONG-FORM PRODUCTION CONTEXT" not in second["custom_instructions"]
    assert "h3_prompt_translation" not in second
    assert "Unrelated library footsteps" not in compile_project(second)["prompt"]


def test_ref2va_materialise_excludes_non_card_source_people_and_assets(tmp_path):
    manager, source, projects, _assets, store_asset = _rig(tmp_path)
    legacy_image = _image(store_asset, "legacy-yu", "gray")
    legacy_image.update(enabled=True, role="reference_image", semantic_role="face")
    legacy_subject = {
        "id": _id(), "name": "Yu", "description": "Legacy source-only person.",
        "asset_ids": [legacy_image["id"]],
    }
    source["assets"].append(copy.deepcopy(legacy_image))
    source["subjects"].append(copy.deepcopy(legacy_subject))
    projects[source["id"]] = copy.deepcopy(source)

    production = manager.create({
        "source_project": source, "brief": "Yuki sits alone.", "source_mode": "ref2va",
    })
    yuki_image = _image(store_asset, "Yuki", "pink")
    yuki = _card("Yuki", [yuki_image["id"]])
    production["cards"]["characters"] = [yuki]
    production = manager.save(production)
    planned = [{
        "title": "Alone", "story": "Yuki sits alone.", "setting": "room",
        "action": "Yuki sits on the sofa.", "ending": "Yuki remains still.",
        "duration": 5, "duration_reason": "single beat", "image_prompt": "Yuki alone",
        "dialogue": [], "card_selection": {"characters": ["Yuki"]},
    }, {
        "title": "Still alone", "story": "Yuki looks up.", "setting": "room",
        "action": "Yuki slowly looks up.", "ending": "Yuki faces the window.",
        "duration": 5, "duration_reason": "single beat", "image_prompt": "Yuki alone",
        "dialogue": [], "card_selection": {"characters": ["Yuki"]},
    }]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    manager.materialise(production["id"], production["segments"][1]["id"])

    assert [subject["name"] for subject in project["subjects"]] == ["Yuki"]
    assert [asset["id"] for asset in project["assets"] if asset.get("enabled")] == [yuki_image["id"]]
    compiled = compile_project(project)["prompt"]
    assert "Legacy source-only person" not in compiled
    assert "legacy-yu" not in compiled
    # Materialisation is non-destructive: the source Studio project still owns
    # its old reference, while the production render copy does not inherit it.
    assert any(subject["name"] == "Yu" for subject in projects[source["id"]]["subjects"])
    assert any(asset["id"] == legacy_image["id"] for asset in projects[source["id"]]["assets"])
    assert [segment["status"] for segment in manager.get(production["id"])["segments"]] == ["ready", "ready"]


def test_merge_plan_drops_offscreen_actor_from_generated_physical_staging():
    project = new_project()
    project["mode"] = "t2va"
    visible_id, voice_id = _id(), _id()
    project["subjects"] = [
        {"id": visible_id, "name": "Visible", "description": "", "asset_ids": []},
        {"id": voice_id, "name": "Voice", "description": "", "asset_ids": []},
    ]
    scene = shot(5)
    scene.update({
        "visible_subject_ids": [visible_id], "offscreen_subject_ids": [voice_id],
        "director_locks": ["visible_subject_ids", "offscreen_subject_ids"],
        "dialogue": [{"id": _id(), "speaker_id": voice_id, "text": "Listen.",
                      "language": "English", "delivery": "calm", "locked": True, "voiceover": True}],
    })
    project["shots"] = [scene]
    project["simple"] = {"directed": True}
    proposal = {
        "shots": [{
            **{key: copy.deepcopy(scene[key]) for key in ("duration", "action", "setting", "camera", "performance", "final_state", "sound", "transition")},
            "visible_subject_ids": [visible_id], "offscreen_subject_ids": [voice_id],
            "scene_contract": {
                "actors": [
                    {"subject_id": visible_id, "activity": "hold", "start": "0s", "action": "watches", "end": "4 seconds"},
                    {"subject_id": voice_id, "activity": "act", "start": "off-screen", "action": "speaks", "end": "off-screen"},
                ],
                "objects": [], "environment": "room", "background_activity": "",
            },
        }],
        "style": copy.deepcopy(project["style"]), "soundscape": "", "music": "", "notes": [],
    }
    merged = merge_plan(project, proposal)
    assert [row["subject_id"] for row in merged["shots"][0]["scene_contract"]["actors"]] == [visible_id]
    actor = merged["shots"][0]["scene_contract"]["actors"][0]
    assert actor["start"] == "in the established opening posture and position"
    assert actor["end"] == "in the declared final posture and position"
    compiled = compile_project(merged)
    assert compiled["valid"] is True, compiled["issues"]


def test_ai_text_card_plan_fills_gaps_without_overwriting_manual_cards(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A and B meet in a library. B speaks."})
    a_image = _image(store_asset, "A", "red")
    a = _card("A", [a_image["id"]], description="USER LOCKED A DESCRIPTION", notes="USER A NOTE")
    production["cards"]["characters"] = [a]
    production = manager.save(production)
    planned = {
        "series_voice_style": "Natural ensemble dialogue with clean studio recording.",
        "characters": [
            {"name": "A", "description": "AI must not replace this", "notes": "AI must not replace this note"},
            {"name": "B", "description": "A composed young librarian with short dark hair.", "notes": "Keeps the brass key."},
        ],
        "wardrobe": [{"name": "B work clothes", "description": "Navy cardigan and white shirt.",
                      "notes": "Same through the library scene.", "owner_character": "B"}],
        "props": [{"name": "Brass key", "description": "Small worn brass key.",
                   "notes": "Continuity-critical.", "owner_character": "B"}],
        "environments": [{"name": "Library", "description": "Warm wooden stacks and a central aisle.",
                          "notes": "Stable shelf layout."}],
        "voices": [{"name": "B voice", "character_name": "B",
                    "description": "Clear, composed young adult voice.", "notes": "Never robotic.",
                    "voice_id": "B_AUTO", "pace": "measured"}],
        "styles": [{"name": "Warm family adventure", "description": "Polished cinematic animation.",
                    "notes": "No plastic surfaces."}],
    }
    result = manager.apply_card_plan(production["id"], planned)
    a_after = next(card for card in result["cards"]["characters"] if card["name"] == "A")
    b_after = next(card for card in result["cards"]["characters"] if card["name"] == "B")
    b_voice = result["cards"]["voices"][0]
    assert a_after["description"] == "USER LOCKED A DESCRIPTION"
    assert a_after["notes"] == "USER A NOTE"
    assert a_after["asset_ids"] == [a_image["id"]]
    assert b_voice["character_card_id"] == b_after["id"]
    assert b_voice["language"] == result["language"]
    assert result["series_voice_style"].startswith("Natural ensemble")
    assert result["card_planner"] == "local_ai"
    assert result["card_plan_source_hash"] == card_plan_source_hash(result)


def test_manual_card_content_prevents_implicit_ai_card_generation(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A enters."})
    assert has_substantive_card_library(production) is False
    production["cards"]["characters"] = [_card("A", description="Hand-authored identity")]
    assert has_substantive_card_library(production) is True


def test_reference_media_without_text_still_allows_ai_text_completion(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A enters."})
    reference = _image(store_asset, "A", "blue")
    production["cards"]["characters"] = [_card("A", [reference["id"]], description="", notes="")]
    assert has_substantive_card_library(production) is False


def test_manual_overview_covers_cards_without_individual_images(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A, B, C and D stand together."})
    production["cards"]["characters"] = [_card(name) for name in ("A", "B", "C", "D")]
    overview = _image(store_asset, "cast-overview", "navy")
    production["overview_asset_ids"]["characters"] = overview["id"]
    production = manager.save(production)
    planned = [{"title": "Group", "story": "A, B, C and D stand together.", "setting": "room",
                "action": "The group faces camera.", "ending": "The group holds.", "duration": 5,
                "duration_reason": "group hold", "dialogue": [], "image_prompt": "four-person group",
                "card_selection": {"characters": ["A", "B", "C", "D"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    result = manager.materialise(production["id"], production["segments"][0]["id"])
    strategy = result["project"]["production_link"]["reference_strategy"]
    assert strategy["image_asset_ids"] == [overview["id"]]
    assert strategy["overview_sources"] == {"characters": "manual"}
    asset = next(item for item in result["project"]["assets"] if item["id"] == overview["id"])
    assert "selected cards: A, B, C, D" in asset["description"]
    assert asset["reference_overview"] is True
    assert asset["reference_card_names"] == ["A", "B", "C", "D"]
    assert [row["name"] for row in asset["reference_card_bindings"]] == ["A", "B", "C", "D"]
    assert all(overview["id"] in subject["asset_ids"] for subject in result["project"]["subjects"]
               if subject["name"] in {"A", "B", "C", "D"})
    assert "CHARACTER AUTHORITY SPLIT" in result["project"]["production_planning_context"]
    compiled = compile_project(result["project"])
    assert compiled["valid"] is True
    assert "assigned image pixels lead directly visible appearance" in compiled["prompt"]
    assert compiled["prompt"].count("<Picture 1> supplies character identity") == 4
    for name in ("A", "B", "C", "D"):
        assert compiled["prompt"].count(name + " stable traits") == 1
    assert "named-card facts:" not in compiled["prompt"]
    assert compiled["prompt"].count("library overview for this clip") == 0


def test_manual_overviews_reduce_cross_category_reference_pressure(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Three people use three props in three outfits across three rooms."})
    for kind, color in (("characters", "red"), ("wardrobe", "green"),
                        ("props", "blue"), ("environments", "gray")):
        production["cards"][kind] = [
            _card(f"{kind}-{index}", [_image(store_asset, f"{kind}-{index}", color)["id"]])
            for index in range(3)]
        production["overview_asset_ids"][kind] = _image(store_asset, f"{kind}-overview", color)["id"]
    production = manager.save(production)
    selection = {kind: [card["name"] for card in production["cards"][kind]]
                 for kind in ("characters", "wardrobe", "props", "environments")}
    planned = [{"title": "Busy scene", "story": production["brief"], "setting": "three rooms",
                "action": "Everyone handles the selected items.", "ending": "They stop.", "duration": 12,
                "duration_reason": "ensemble action", "dialogue": [], "image_prompt": "ensemble",
                "card_selection": selection}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    result = manager.materialise(production["id"], production["segments"][0]["id"])
    strategy = result["project"]["production_link"]["reference_strategy"]
    assert len(strategy["image_asset_ids"]) <= 9
    assert strategy["overview_sources"]
    assert set(strategy["overview_sources"].values()) == {"manual"}
    assert "characters" not in strategy["overview_sources"]


def test_dense_cast_uses_clip_specific_overview_even_when_nine_slots_fit(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Eight friends inspect a clock."})
    characters = [_card(f"Actor {index}", [_image(store_asset, f"actor-{index}", "blue")["id"]])
                  for index in range(1, 9)]
    props = [_card("Clock", [_image(store_asset, "clock", "gold")["id"]])]
    cast_overview = _image(store_asset, "full-cast-overview", "navy")
    prop_overview = _image(store_asset, "prop-overview", "gray")
    production["cards"].update({"characters": characters, "props": props})
    production["overview_asset_ids"].update({"characters": cast_overview["id"],
                                              "props": prop_overview["id"]})
    production = manager.save(production)
    planned = [{"title": "Inspection", "story": "Eight friends inspect one clock.", "setting": "tower",
                "action": "The group looks at the clock.", "ending": "They hold.", "duration": 8,
                "duration_reason": "group reaction", "dialogue": [], "image_prompt": "group at clock",
                "card_selection": {"characters": [card["name"] for card in characters],
                                   "props": ["Clock"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")

    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    strategy = project["production_link"]["reference_strategy"]

    assert len(strategy["image_asset_ids"]) == 2
    assert cast_overview["id"] not in strategy["image_asset_ids"]
    assert not set(card["asset_ids"][0] for card in characters).intersection(strategy["image_asset_ids"])
    assert props[0]["asset_ids"][0] in strategy["image_asset_ids"]
    assert prop_overview["id"] not in strategy["image_asset_ids"]
    assert strategy["overview_sources"] == {"characters": "automatic"}
    overview = next(asset for asset in project["assets"]
                    if asset.get("reference_card_kind") == "characters")
    assert overview["reference_card_names"] == [card["name"] for card in characters]


def test_explicit_heuristic_cast_does_not_expand_and_badge_form_is_not_a_body(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A moves while B watches."})
    characters = [_card(name, [_image(store_asset, name, colour)["id"]]) for name, colour in
                  (("A", "red"), ("B", "blue"), ("Courage Star", "yellow"))]
    production["cards"]["characters"] = characters
    production = manager.save(production)
    planned = [{"title": "Move", "story": "A moves while B is discussed. Courage Star remains in badge form.",
                "setting": "room", "action": "A crosses the room; Courage Star stays as a badge on A.",
                "ending": "A stops.", "duration": 5, "duration_reason": "one move", "dialogue": [],
                "image_prompt": "A crossing", "card_selection": {"characters": ["A", "Courage Star"]}}]
    production = manager.apply_plan(production["id"], planned, "local_heuristic")

    segment = production["segments"][0]
    assert segment["card_selection"]["characters"] == ["A"]
    project = manager.materialise(production["id"], segment["id"])["project"]
    assert [subject["name"] for subject in project["subjects"]] == ["A"]
    assert len(project["production_link"]["reference_strategy"]["image_asset_ids"]) == 1


def test_translated_cast_line_binds_local_alias_and_canonicalises_dialogue_speaker(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    brief = """**本组出场：**Mimi、Pokke、发条维修守卫。
分镜15｜02:00—02:08
发条维修守卫迈近Pokke，维修钳指向下方平台。
守卫｜礼貌认真：“Please cooperate with your removal.”
Pokke后退一步。"""
    production = manager.create({"source_project": source, "brief": brief, "language": "en"})
    characters = [_card(name, [_image(store_asset, name, colour)["id"]]) for name, colour in
                  (("Mimi", "pink"), ("Pokke", "green"), ("The Clockwork Keeper", "gold"))]
    production["cards"]["characters"] = characters
    production["episodes"] = [{"title": "Episode 1", "logline": "", "story": "",
                               "character_card_ids": [card["id"] for card in characters],
                               "continuity_notes": ""}]
    production = manager.save(production)

    aliases = character_aliases(production)
    keeper = characters[2]
    assert "发条维修守卫" in aliases[keeper["id"]]
    assert "守卫" in aliases[keeper["id"]]

    planned = fallback_segments(brief, language="en")
    production = manager.apply_plan(production["id"], planned, "local_heuristic")
    segment = production["segments"][0]
    assert set(segment["card_selection"]["characters"]) == {"Pokke", "The Clockwork Keeper"}
    assert segment["dialogue"][0]["speaker"] == "The Clockwork Keeper"
    result = manager.materialise(production["id"], segment["id"])
    assert {subject["name"] for subject in result["project"]["subjects"]} == {"Pokke", "The Clockwork Keeper"}
    assert "The Clockwork Keeper" in result["project"]["story"]["text"]
    assert "发条维修守卫" not in result["project"]["story"]["text"]
    assert "The Clockwork Keeper" in result["project"]["shots"][0]["action"]
    assert "发条维修守卫" not in result["project"]["shots"][0]["action"]
    assert result["production"]["segments"][0]["reference_strategy_version"] == REFERENCE_STRATEGY_VERSION


def test_render_character_identity_removes_exact_duplicate_sentences():
    sentence = "A maintenance guard operating on strict, polite, but inflexible rules."
    rendered = render_character_identity({"description": sentence + " " + sentence,
                                          "notes": "Compact brass body."})

    assert rendered.count(sentence) == 1
    assert rendered.endswith("Compact brass body.")


def test_cast_parenthetical_state_is_not_registered_as_a_character_alias(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source,
                                 "brief": "本组出场：Courage Star（徽章形态）。"})
    star = _card("Courage Star")
    production["cards"]["characters"] = [star]
    production["episodes"] = [{"title": "Episode 1", "logline": "", "story": "",
                               "character_card_ids": [star["id"]], "continuity_notes": ""}]
    production = manager.save(production)

    aliases = character_aliases(production)[star["id"]]

    assert "courage star" in aliases
    assert "徽章形态" not in aliases


def test_old_prepared_reference_strategy_is_marked_stale_without_changing_cards(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A enters."})
    character = _card("A", [_image(store_asset, "a", "red")["id"]])
    production["cards"]["characters"] = [character]
    production = manager.save(production)
    planned = [{"title": "Arrival", "story": "A enters.", "setting": "room", "action": "A enters.",
                "ending": "A stops.", "duration": 5, "duration_reason": "one action", "dialogue": [],
                "image_prompt": "A enters", "card_selection": {"characters": ["A"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    production = manager.materialise(production["id"], production["segments"][0]["id"])["production"]
    original_cards = copy.deepcopy(production["cards"])
    production["segments"][0]["reference_strategy_version"] = 0

    checked = manager.validate(production)

    assert checked["cards"] == original_cards
    assert checked["segments"][0]["status"] == "stale"
    assert any("参考图分配规则已升级" in reason for reason in checked["segments"][0]["stale_reasons"])


def test_character_name_does_not_fuzzily_select_all_owned_props(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Pokke crosses the room."})
    pokke = _card("Pokke", [_image(store_asset, "pokke", "green")["id"]])
    pencil = _card("Pokke's Pencil", [_image(store_asset, "pencil", "yellow")["id"]],
                   owner_card_id=pokke["id"])
    clock = _card("Pokke's Small Clock", [_image(store_asset, "clock", "gold")["id"]],
                  owner_card_id=pokke["id"])
    production["cards"]["characters"] = [pokke]
    production["cards"]["props"] = [pencil, clock]
    production = manager.save(production)
    planned = [{"title": "Crossing", "story": "Pokke crosses the room.", "setting": "room",
                "action": "Pokke walks.", "ending": "Pokke stops.", "duration": 5,
                "duration_reason": "one action", "dialogue": [], "image_prompt": "Pokke walking",
                "card_selection": {"characters": ["Pokke"]}}]
    production = manager.apply_plan(production["id"], planned, "local_heuristic")

    result = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    strategy = result["production_link"]["reference_strategy"]

    assert strategy["image_asset_ids"] == pokke["asset_ids"]


def test_offline_episode_plan_keeps_requested_count(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "One. Two. Three. Four.", "episode_count": 3})
    assert len(fallback_episodes(production)) == 3


def test_style_image_analysis_outranks_custom_text_and_preset(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A person crosses a quiet room.",
                                 "visual_style_preset": "custom", "visual_style_custom": "soft pencil motion",
                                 "narrative_style": "custom", "narrative_style_custom": "circular ending"})
    style_asset = _image(store_asset, "style-reference", "purple")
    production["cards"]["styles"] = [_card("Lavender reference", [style_asset["id"]],
                                                     image_analysis="low contrast violet palette; fine paper grain")]
    production = manager.save(production)
    payload = planning_payload(production)
    assert '"style_source_priority": "style-card image analysis > custom visual style > visual style bible > named preset"' in payload
    assert '"custom_visual_style": "soft pencil motion"' in payload
    assert '"image_analysis": "low contrast violet palette; fine paper grain"' in payload
    planned = [{"title": "Crossing", "story": "A person crosses a quiet room.", "setting": "quiet room",
                "action": "The person crosses the room.", "ending": "The person reaches the door.",
                "duration": 6, "duration_reason": "walking pace", "dialogue": [],
                "image_prompt": "person crossing a room", "card_selection": {}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    result = manager.materialise(production["id"], production["segments"][0]["id"])
    strategy = result["project"]["production_link"]["reference_strategy"]
    assert strategy["style_context_asset_ids"] == [style_asset["id"]]
    materialised = next(asset for asset in result["project"]["assets"] if asset["id"] == style_asset["id"])
    assert materialised["role"] == "context"
    assert materialised["approved_observation"] == "low contrast violet palette; fine paper grain"
    assert "highest-priority visual style authority" in materialised["description"]


def test_legacy_production_gains_safe_task_timing_prompt_and_keyframe_defaults(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A walks into the library."})
    planned = [{"title": "Arrival", "story": "A arrives.", "setting": "library",
                "action": "A enters.", "ending": "A stops.", "duration": 5,
                "duration_reason": "one action", "dialogue": [], "image_prompt": "A in library",
                "card_selection": {}}]
    production = manager.apply_plan(production["id"], planned, "local_heuristic")
    production.pop("task_state")
    production.pop("timings")
    production.pop("auto_continue_previous")
    for key in ("keyframe_asset_ids", "image_run_ids", "workflow_profile_id", "video_prompt"):
        production["segments"][0].pop(key)
    production = manager.validate(production)
    segment = production["segments"][0]
    assert production["task_state"] == "active"
    assert production["auto_continue_previous"] is False
    assert production["timings"] == {"episode_plan_seconds": None, "storyboard_plan_seconds": None,
                                     "merge_seconds": None}
    assert segment["keyframe_asset_ids"] == []
    assert segment["image_run_ids"] == []
    assert segment["workflow_profile_id"] == "builtin"
    assert segment["video_prompt"] == ""


def test_automatic_continuation_is_opt_in_and_keeps_the_source_project_untouched(tmp_path):
    manager, source, projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A crosses the library."})
    assert production["auto_continue_previous"] is False
    production = manager.update(production["id"], {"auto_continue_previous": True})
    assert production["auto_continue_previous"] is True
    assert "continuation_source" not in projects[source["id"]].get("comfy_render", {})
    with pytest.raises(ValueError, match="Automatic continuation"):
        manager.update(production["id"], {"auto_continue_previous": "yes"})


def test_classic_voice_style_does_not_import_the_full_series_voice_roster(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "language": "en",
        "series_voice_style": "Polished animated dialogue.\n#### The characters should have distinct voices:\nA: bright.\nNox: smoky."})
    actor = _card("A", [_image(store_asset, "actor-a", "blue")["id"]])
    voice = _card("A voice", character_card_id=actor["id"], description="Bright and clear.")
    production["cards"].update({"characters": [actor], "voices": [voice]})
    manager.save(production)
    planned = [{"title": "Greeting", "story": "A speaks.", "setting": "library",
        "action": "A waves.", "ending": "A stays by the door.", "duration": 5,
        "duration_reason": "one wave", "dialogue": [{"speaker": "A", "text": "Hello.",
        "language": "English", "voiceover": False}], "image_prompt": "A by the door",
        "card_selection": {"characters": ["A"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    result = compile_project(project)
    assert result["valid"], result["issues"]
    assert "Bright and clear." in result["prompt"]
    assert "Nox: smoky" not in result["prompt"]
    assert "Exact named visible roster:" in result["prompt"]
    assert "Continuity safeguards: no duplicate instance" in result["prompt"]


def test_clip_owned_keyframes_and_workflow_are_materialised_without_touching_source(tmp_path):
    manager, source, projects, _assets, store_asset = _rig(tmp_path)
    source["comfy_render"] = {"continuation_source": "mmh3/old-unrelated/video.mmh3"}
    source["comfy_render"]["continuation_overlap_frames"] = 39
    source["comfy_render"]["duration_basis"] = "new_footage"
    projects[source["id"]] = copy.deepcopy(source)
    production = manager.create({"source_project": source, "brief": "A crosses a room."})
    planned = [{"title": "Cross", "story": "A crosses.", "setting": "room",
                "action": "A crosses the room.", "ending": "A reaches the door.", "duration": 6,
                "duration_reason": "walking", "dialogue": [], "image_prompt": "A crossing",
                "card_selection": {}}]
    production = manager.apply_plan(production["id"], planned, "local_heuristic")
    segment = production["segments"][0]
    first = _image(store_asset, "clip-start", "red")
    second = _image(store_asset, "clip-detail", "blue")
    manager.attach_asset(production["id"], segment["id"], first)
    production = manager.attach_asset(production["id"], segment["id"], second)
    custom_workflow_id = _id()
    production["segments"][0]["workflow_profile_id"] = custom_workflow_id
    production = manager.save(production)
    result = manager.materialise(production["id"], segment["id"])
    assert result["production"]["segments"][0]["keyframe_asset_ids"] == [first["id"], second["id"]]
    assert result["project"]["comfy_render"]["workflow_profile_id"] == custom_workflow_id
    assert "continuation_source" not in result["project"]["comfy_render"]
    assert "continuation_overlap_frames" not in result["project"]["comfy_render"]
    assert "duration_basis" not in result["project"]["comfy_render"]
    assert projects[source["id"]]["comfy_render"]["continuation_source"] == "mmh3/old-unrelated/video.mmh3"
    clip_assets = [item for item in result["project"]["assets"] if item["id"] in {first["id"], second["id"]}]
    assert len(clip_assets) == 2
    assert all(item["role"] == "context" and item["production_segment_id"] == segment["id"] for item in clip_assets)
    assert not any(item["id"] in {first["id"], second["id"]} for item in projects[source["id"]]["assets"])


def test_video_workflow_library_keeps_builtin_and_validates_h3_api_graph(tmp_path):
    manager = VideoWorkflowManager(tmp_path)
    graph = {
        "1": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"prompt": ""}},
        "2": {"class_type": "SaveVideo", "inputs": {"video": ["1", 0], "filename_prefix": "test"}},
    }
    created = manager.create({"name": "My H3 profile", "description": "local", "graph": graph})
    wrapped = manager.create({"name": "Queued H3 profile", "graph": {"prompt": graph}})
    assert manager.list()[0]["id"] == "builtin"
    assert manager.get(created["id"])["modes"] == ["ref2va"]
    assert manager.get(wrapped["id"])["modes"] == ["ref2va"]
    with pytest.raises(ValueError, match="exactly one MiniMax H3"):
        manager.create({"name": "Unsafe", "graph": {"1": {"class_type": "SaveVideo", "inputs": {}}}})
    assert manager.delete(created["id"])["deleted"] is True
    assert manager.delete(wrapped["id"])["deleted"] is True


def test_delete_production_archives_record_and_preserves_linked_projects(tmp_path):
    manager, source, projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A scene."})
    production["cards"]["characters"] = [_card("Preserved hero")]
    production = manager.save(production)
    result = manager.delete(production["id"])
    assert result["linked_projects_preserved"] is True
    assert result["card_library_preserved"] is True
    preserved = manager.get_card_collection(result["card_collection_id"])
    assert preserved["name"] == production["card_collection_name"]
    assert preserved["cards"]["characters"][0]["name"] == "Preserved hero"
    assert source["id"] in projects
    assert list((tmp_path / "data" / "production_archive").glob(production["id"] + "-*.json"))
    with pytest.raises(ValueError, match="Production not found"):
        manager.get(production["id"])


@pytest.mark.parametrize("prompt_version", ["classic", "continuity_director"])
def test_new_production_excludes_prior_clip_directions_in_both_prompt_versions(tmp_path, prompt_version):
    manager, source, projects, _assets, store_asset = _rig(tmp_path)
    source["production_link"] = {"production_id": _id(), "segment_id": _id()}
    source["custom_instructions"] = "Old scene: Yuki waits in the living room."
    projects[source["id"]] = copy.deepcopy(source)
    production = manager.create({"source_project": source, "brief": "Pokke visits the tea garden.",
                                 "prompt_version": prompt_version, "language": "en"})
    production["cards"]["characters"] = [_card("Pokke", [_image(store_asset, "Pokke", "green")["id"]])]
    production = manager.save(production)
    planned = [{"title": "Tea garden", "story": "Pokke visits the tea garden.",
                "setting": "tea garden", "action": "Pokke looks at the clock.",
                "ending": "Pokke remains by the clock.", "duration": 5,
                "duration_reason": "one action", "image_prompt": "Pokke at a clock",
                "dialogue": [], "card_selection": {"characters": ["Pokke"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    compiled = compile_project(project)
    assert compiled["valid"], compiled["issues"]
    assert "living room" not in compiled["prompt"]
    assert not manager.has_inherited_clip_directions(production, project)
    stale_project = copy.deepcopy(project)
    stale_project["custom_instructions"] = source["custom_instructions"] + "\n" + project["custom_instructions"]
    assert manager.has_inherited_clip_directions(production, stale_project)
    assert source["custom_instructions"] == projects[source["id"]]["custom_instructions"]


def test_matching_voice_block_is_not_misclassified_as_inherited_scene_direction(tmp_path):
    manager, source, projects, _assets, _store_asset = _rig(tmp_path)
    voice_block = (
        "VOICE DIRECTION — CURRENT CLIP ONLY\n\n"
        "Use the series voice style only for explicitly tagged dialogue.\n\n"
        "Voice Card — Pokke:\nA lively recurring companion voice."
    )
    source["production_link"] = {"production_id": _id(), "segment_id": _id()}
    source["custom_instructions"] = voice_block
    projects[source["id"]] = copy.deepcopy(source)
    production = manager.create({"source_project": source, "brief": "Pokke waits by the clock."})
    current = copy.deepcopy(source)
    current["production_link"] = {"production_id": production["id"], "segment_id": _id()}
    current["custom_instructions"] = voice_block
    assert not manager.has_inherited_clip_directions(production, current)

    current["custom_instructions"] = "Old scene: Yuki waits in the living room.\n" + voice_block
    source["custom_instructions"] = "Old scene: Yuki waits in the living room."
    projects[source["id"]] = copy.deepcopy(source)
    assert manager.has_inherited_clip_directions(production, current)


@pytest.mark.parametrize("prompt_version", ["classic", "continuity_director"])
def test_render_cards_drop_old_plot_and_name_object_holder(tmp_path, prompt_version):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    character = _card("Pokke", [_image(store_asset, "Pokke", "green")["id"]])
    character["description"] = (
        "Mint-green backpack with a zipper mouth.\n"
        "Signature Details / Accessories:\nTwo pencil antennae.\n"
        "Currently waiting in Yuki's living room.\n"
        "He possesses a cloth from the previous action."
    )
    production = manager.create({"source_project": source, "brief": "Pokke opens the book.",
                                 "prompt_version": prompt_version, "language": "en"})
    production["cards"]["characters"] = [character]
    production = manager.save(production)
    planned = [{"title": "The book", "story": "Pokke opens the book.", "setting": "tea garden",
                "action": "Pokke opens the book.", "ending": "Pokke holds it open.",
                "duration": 5, "duration_reason": "single action", "image_prompt": "Pokke with book",
                "dialogue": [], "card_selection": {"characters": ["Pokke"]}}]
    production = manager.apply_plan(production["id"], planned, "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    holder_id = project["shots"][0]["visible_subject_ids"][0]
    project["shots"][0]["scene_contract"] = {
        "actors": [], "environment": "tea garden", "background_activity": "",
        "objects": [{"entity_id": _id(), "name": "Ancient Book", "description": "an open tome",
                     "count": 1, "start": f"held by {holder_id}", "end": f"held by {holder_id}"}],
    }
    result = compile_project(project)
    assert result["valid"], result["issues"]
    assert "Mint-green backpack" in result["prompt"]
    assert "Two pencil antennae" in result["prompt"]
    assert "Yuki" not in result["prompt"]
    assert "previous action" not in result["prompt"]
    assert ("held by <Subject 1> Pokke" if prompt_version == "classic" else "held by Pokke") in result["prompt"]
    assert holder_id not in result["prompt"]
    assert "Yuki's living room" in manager.get(production["id"])["cards"]["characters"][0]["description"]


def test_render_character_identity_preserves_visual_text_only():
    card = {"description": "Dark blue scarf.\n現在、前の場面を思い出している。",
            "notes": "月形の耳。\n本段正在等候主角。"}
    result = render_character_identity(card)
    assert "Dark blue scarf" in result
    assert "月形の耳" in result
    assert "前の場面" not in result
    assert "本段" not in result


def test_temporal_cast_guard_keeps_exit_reference_but_blocks_undeclared_return(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    hero = _card("Hero", [_image(store_asset, "hero", "blue")["id"]])
    keeper = _card("Clockwork Keeper", [_image(store_asset, "keeper", "gold")["id"]])
    production = manager.create({"source_project": source, "brief": "Hero crosses the clockwork hall."})
    production["cards"]["characters"] = [hero, keeper]
    production = manager.save(production)
    empty = {key: [] for key in
             ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")}
    first = copy.deepcopy(empty)
    first.update(visible_start=["Hero", "Clockwork Keeper"],
                 visible_end=["Hero", "Clockwork Keeper"])
    reset = copy.deepcopy(empty)
    reset.update(visible_start=["Hero", "Clockwork Keeper"], visible_end=["Hero"],
                 exits=["Clockwork Keeper"])
    mistaken_return = copy.deepcopy(empty)
    mistaken_return.update(visible_start=["Hero", "Clockwork Keeper"],
                            visible_end=["Hero", "Clockwork Keeper"])
    planned = [
        _planned_clip("The meeting", ["Hero", "Clockwork Keeper"], first),
        _planned_clip("The keeper resets below", ["Hero", "Clockwork Keeper"], reset, "state_change"),
        _planned_clip("Hero continues upstairs", ["Hero", "Clockwork Keeper"], mistaken_return, "continuous"),
    ]

    production = manager.apply_plan(production["id"], planned, "local_ai")
    exit_clip, later_clip = production["segments"][1:]

    # The exiting character still needs its reference in the departure clip.
    assert exit_clip["card_selection"]["characters"] == ["Hero", "Clockwork Keeper"]
    assert exit_clip["cast_timeline"]["visible_end"] == ["Hero"]
    # A later accidental roster mention may not resurrect it.
    assert later_clip["card_selection"]["characters"] == ["Hero"]
    assert later_clip["cast_timeline"]["visible_start"] == ["Hero"]
    assert later_clip["cast_timeline"]["visible_end"] == ["Hero"]
    assert later_clip["cast_timeline"]["mentioned_only"] == ["Clockwork Keeper"]
    assert "previously exited" in later_clip["continuity_warnings"][0]

    departure_project = manager.materialise(production["id"], exit_clip["id"])["project"]
    assert "Exit physically during this clip: Clockwork Keeper" in departure_project["production_planning_context"]
    assert "FINAL-FRAME CAST LOCK: show exactly Hero" in departure_project["shots"][0]["final_state"]
    later_project = manager.materialise(production["id"], later_clip["id"])["project"]
    later_visible = later_project["shots"][0]["visible_subject_ids"]
    assert [subject["name"] for subject in later_project["subjects"]
            if subject["id"] in later_visible] == ["Hero"]


def test_temporal_cast_guard_allows_an_explicit_reentry(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Hero and Guard separate, then reunite."})
    production["cards"]["characters"] = [_card("Hero"), _card("Guard")]
    production = manager.save(production)
    empty = {key: [] for key in
             ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")}
    together = copy.deepcopy(empty)
    together.update(visible_start=["Hero", "Guard"], visible_end=["Hero", "Guard"])
    departure = copy.deepcopy(empty)
    departure.update(visible_start=["Hero", "Guard"], visible_end=["Hero"], exits=["Guard"])
    return_timeline = copy.deepcopy(empty)
    return_timeline.update(visible_start=["Hero"], visible_end=["Hero", "Guard"], enters=["Guard"])
    production = manager.apply_plan(production["id"], [
        _planned_clip("Together", ["Hero", "Guard"], together),
        _planned_clip("Guard leaves", ["Hero", "Guard"], departure, "hard_cut"),
        _planned_clip("Guard returns", ["Hero", "Guard"], return_timeline, "hard_cut"),
    ], "local_ai")

    returned = production["segments"][2]
    assert returned["card_selection"]["characters"] == ["Hero", "Guard"]
    assert returned["cast_timeline"]["enters"] == ["Guard"]
    assert returned["continuity_warnings"] == []


def test_temporal_cast_guard_does_not_make_a_camera_exit_permanent(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A group climbs a tower."})
    production["cards"]["characters"] = [_card("Hero"), _card("Friend"), _card("Guard")]
    production = manager.save(production)
    empty = {key: [] for key in
             ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")}
    together = copy.deepcopy(empty)
    together.update(visible_start=["Hero", "Friend", "Guard"],
                    visible_end=["Hero", "Friend", "Guard"])
    through_gate = copy.deepcopy(empty)
    through_gate.update(visible_start=["Hero", "Friend", "Guard"], visible_end=["Guard"],
                        exits=["Hero", "Friend"])
    next_stair = copy.deepcopy(empty)
    next_stair.update(visible_start=["Hero", "Friend"], visible_end=["Hero", "Friend"])

    production = manager.apply_plan(production["id"], [
        _planned_clip("The group reaches the stair gate", ["Hero", "Friend", "Guard"], together),
        _planned_clip("Hero and Friend climb through the gate while Guard follows", ["Hero", "Friend", "Guard"],
                      through_gate, "hard_cut"),
        _planned_clip("Hero and Friend continue climbing the adjoining stairs", ["Hero", "Friend"],
                      next_stair, "continuous"),
    ], "local_ai")

    following = production["segments"][2]
    assert following["card_selection"]["characters"] == ["Hero", "Friend"]
    assert following["cast_timeline"]["visible_start"] == ["Hero", "Friend"]
    assert following["cast_timeline"]["visible_end"] == ["Hero", "Friend"]
    assert following["cast_timeline"]["mentioned_only"] == []
    assert following["continuity_warnings"] == []


def test_legacy_storyboard_requires_replanning_before_prompt_rebuild(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A crosses a room."})
    production["cards"]["characters"] = [_card("A")]
    production = manager.save(production)
    timeline = {"visible_start": ["A"], "visible_end": ["A"], "enters": [], "exits": [],
                "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(production["id"], [
        _planned_clip("Crossing", ["A"], timeline)
    ], "local_ai")
    production = manager.materialise(production["id"], production["segments"][0]["id"])["production"]
    legacy = copy.deepcopy(production)
    legacy["segments"][0].pop("cast_timeline", None)
    legacy["segments"][0].pop("cast_timeline_version", None)
    legacy = manager.save(legacy)

    assert legacy["segments"][0]["status"] == "stale"
    assert any("重新规划" in reason for reason in legacy["segments"][0]["stale_reasons"])
    with pytest.raises(ValueError, match="Replan this episode"):
        manager.materialise(legacy["id"], legacy["segments"][0]["id"])


def test_same_count_replan_preserves_clip_and_take_links_as_stale(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A waits, then leaves."})
    production["cards"]["characters"] = [_card("A")]
    production = manager.save(production)
    first_timeline = {"visible_start": ["A"], "visible_end": ["A"], "enters": [], "exits": [],
                      "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(production["id"], [
        _planned_clip("Waiting", ["A"], first_timeline)
    ], "local_ai")
    production = manager.materialise(production["id"], production["segments"][0]["id"])["production"]
    original = production["segments"][0]
    original_id, original_project = original["id"], original["project_id"]
    production["segments"][0]["last_video_run_id"] = _id()
    production = manager.save(production)
    last_run = production["segments"][0]["last_video_run_id"]
    exit_timeline = {"visible_start": ["A"], "visible_end": [], "enters": [], "exits": ["A"],
                     "offscreen": [], "mentioned_only": []}

    replanned = manager.apply_plan(production["id"], [
        _planned_clip("A leaves", ["A"], exit_timeline, "state_change")
    ], "local_ai")
    revised = replanned["segments"][0]

    assert revised["id"] == original_id
    assert revised["project_id"] == original_project
    assert revised["last_video_run_id"] == last_run
    assert revised["status"] == "stale"
