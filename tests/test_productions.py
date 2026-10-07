import copy
import io
import uuid

import pytest
from PIL import Image, ImageDraw

from backend.compiler import compile_project
from backend.productions import (CARD_KINDS, REFERENCE_STRATEGY_VERSION, ProductionManager, _primary_character_view,
                                 _segment_hash_with_empty_prop_state,
                                 authored_internal_timing,
                                 card_plan_source_hash, character_aliases, current_episode_story,
                                 character_presence_roles,
                                 continuity_visual_lock, device_screen_geometry_lock,
                                 effective_temporal_cast_lock, inscribed_prop_continuity_lock,
                                 episode_timing_targets, fallback_episodes, fallback_segments,
                                 fit_planned_durations, has_substantive_card_library,
                                 locked_timed_dialogue, planning_payload, storyboard_planning_chunks,
                                 normalise_segment, production_schema_for_story, render_character_identity,
                                 production_source_manifest,
                                 reconcile_transition_contract,
                                 render_visual_style,
                                 scoped_character_bible, script_dialogue, segment_hash, shot_preflight_for_segment,
                                 storyboard_output_token_budget,
                                 timed_clip_groups, timed_group_story, timed_story_beats,
                                 validate_planned_chunk_source_contract)
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


def test_character_sheet_crop_keeps_one_primary_identity_view():
    sheet = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(sheet)
    draw.rounded_rectangle((45, 45, 245, 505), 28, fill="red", outline="black", width=6)
    draw.rounded_rectangle((330, 60, 510, 500), 28, fill="blue", outline="black", width=6)
    draw.rounded_rectangle((575, 70, 735, 495), 28, fill="green", outline="black", width=6)
    for left, top in ((770, 50), (855, 50), (770, 145), (855, 145)):
        draw.ellipse((left, top, left + 60, top + 60), fill="purple", outline="black", width=4)

    cropped = _primary_character_view(sheet)

    assert cropped.width < sheet.width * .45
    colours = {colour for _count, colour in cropped.resize((80, 80)).getcolors(maxcolors=80 * 80)}
    assert any(red > 180 and green < 100 and blue < 100 for red, green, blue in colours)
    assert not any(blue > 180 and red < 100 and green < 100 for red, green, blue in colours)


def test_render_style_removes_reference_sheet_layout_but_keeps_art_direction():
    source = ("Stylized 3D animated character sheet, anthropomorphic animal design, "
              "front side back turnaround, expression sheet, clean white background, "
              "detailed fur, cinematic lighting")
    cleaned = render_visual_style(source)

    assert "character sheet" not in cleaned.casefold()
    assert "turnaround" not in cleaned.casefold()
    assert "expression sheet" not in cleaned.casefold()
    assert "white background" not in cleaned.casefold()
    assert "detailed fur" in cleaned
    assert "cinematic lighting" in cleaned

    production = {"visual_style_custom": source, "visual_style_preset": "cinematic_realism",
                  "style_bible": ""}
    lock = continuity_visual_lock(production, {"styles": []})
    assert "appearance evidence only" in lock
    assert "exactly one spatial instance of each named subject" in lock


def test_individual_character_reference_uses_cached_single_view_derivative(tmp_path):
    manager, source, _projects, assets, store_asset = _rig(tmp_path)
    sheet = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(sheet)
    draw.rounded_rectangle((40, 35, 245, 515), 28, fill="red", outline="black", width=6)
    draw.rounded_rectangle((330, 55, 520, 510), 28, fill="blue", outline="black", width=6)
    draw.ellipse((680, 70, 780, 170), fill="purple", outline="black", width=5)
    buffer = io.BytesIO()
    sheet.save(buffer, "PNG")
    original = store_asset(buffer.getvalue(), "hero_sheet.png", "image/png")
    production = manager.create({"source_project": source, "brief": "Hero enters."})
    card = _card("Hero", [original["id"]])
    production["cards"]["characters"] = [card]

    derived = manager._ensure_character_identity(production, card)

    assert derived != original["id"]
    assert manager._ensure_character_identity(production, card) == derived
    meta = assets[derived]
    with Image.open(manager.data_dir / "assets" / derived / meta["filename"]) as image:
        assert image.width < sheet.width * .4
        assert image.height >= sheet.height * .8


def test_local_ai_visible_action_recovers_omitted_character_but_keeps_voice_offscreen(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Underwater encounter."})
    cj, rick, creature = (_card("CJ"), _card("Rick Park"), _card("Anchor-Worm"))
    production["cards"]["characters"] = [cj, rick, creature]
    segment = {
        "title": "Reveal", "story": "The creature appears.", "setting": "Flooded tunnel",
        "action": ("CJ swims forward. The Anchor-Worm glides along the ceiling. "
                   "Rick Park's voice crackles over comms."),
        "ending": "CJ faces the Anchor-Worm.", "image_prompt": "CJ and the Anchor-Worm underwater",
        "dialogue": [{"speaker": "Rick Park", "text": "Pull back.", "voiceover": True}],
        "card_selection_source": "local_ai",
        "card_selection": {"characters": ["Anchor-Worm"], "wardrobe": [], "props": [],
                           "environments": [], "voices": []},
        "cast_timeline": {"visible_start": [], "visible_end": ["Anchor-Worm"],
                          "enters": ["Anchor-Worm"], "exits": [],
                          "offscreen": ["Rick Park"], "mentioned_only": ["CJ"]},
    }

    relevant = manager._relevant_cards(production, segment)

    assert {card["name"] for card in relevant["characters"]} == {"CJ", "Rick Park", "Anchor-Worm"}
    roles = character_presence_roles(production, segment)
    assert roles[cj["id"]] == "physical"
    assert roles[rick["id"]] == "offscreen"
    assert roles[creature["id"]] == "physical"


def test_materialise_separates_local_actor_phone_actor_and_mentioned_recipient(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    koko = _card("Koko", [_image(store_asset, "Koko", "pink")["id"]])
    besi = _card("Besi", [_image(store_asset, "Besi", "blue")["id"]])
    production = manager.create({"source_project": source, "brief": "Koko calls Besi.", "language": "en"})
    production["cards"]["characters"] = [koko, besi]
    manager.save(production)
    production = manager.apply_plan(production["id"], [{
        "title": "Call", "story": "Koko calls Besi.", "setting": "Koko's room during a video call",
        "action": "Koko holds her phone. Besi's face appears only on the phone screen.",
        "ending": "Koko smiles at the screen.", "duration": 5, "duration_reason": "one reaction",
        "image_prompt": "Koko watches Besi on the phone screen", "dialogue": [],
        "card_selection": {"characters": ["Koko", "Besi"]},
    }], "local_ai")

    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    names = {subject["id"]: subject["name"] for subject in project["subjects"]}
    scene = project["shots"][0]
    assert [names[ident] for ident in scene["visible_subject_ids"]] == ["Koko"]
    assert [names[ident] for ident in scene["display_subject_ids"]] == ["Besi"]
    assert scene["imagined_subject_ids"] == []
    assert "DEVICE INTERACTION LOCK" in scene["action"]
    assert "SCREEN-VIEW GEOMETRY LOCK" in scene["action"]
    assert "do not also show the holder's unobstructed frontal face" in scene["action"]
    assert "The only person permitted inside the display is Besi" in scene["action"]
    compiled = compile_project(project)
    assert compiled["valid"], compiled["issues"]
    assert "REMOTE DISPLAY CAST" in compiled["prompt"]
    assert "Principal cast in this shot: exactly 1" in compiled["prompt"]

    # Merely naming the recipient of a private message must not recruit that
    # person's image or body into the writer's room.
    segment = production["segments"][0]
    segment.update(
        story="Koko writes a private love note to Besi.",
        action="Koko writes 'Besi, I love you' on a card and smiles.",
        image_prompt="Koko writing alone at her desk",
        card_selection={"characters": ["Koko"], "wardrobe": [], "props": [],
                        "environments": [], "voices": []},
        cast_timeline={"visible_start": ["Koko"], "visible_end": ["Koko"],
                       "enters": [], "exits": [], "offscreen": [],
                       "mentioned_only": ["Besi"]},
        card_selection_source="local_ai")
    production = manager.save(production)
    project = manager.materialise(production["id"], segment["id"])["project"]
    scene = project["shots"][0]
    assert [subject["name"] for subject in project["subjects"]] == ["Koko"]
    assert len(scene["visible_subject_ids"]) == 1
    assert scene["display_subject_ids"] == []
    assert "EMOTIONAL SUBTEXT LOCK" in scene["action"]


def test_online_only_canon_repairs_planner_colocation_and_survives_final_prompt(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    koko = _card("Koko", [_image(store_asset, "Koko", "pink")["id"]])
    besi = _card("Besi", [_image(store_asset, "Besi", "blue")["id"]])
    production = manager.create({
        "source_project": source, "language": "en",
        "brief": ("Koko and Besi can only be together online. They remain physically separate "
                  "and never share the same physical room."),
    })
    production["cards"]["characters"] = [koko, besi]
    manager.save(production)
    production = manager.apply_plan(production["id"], [{
        "title": "Remote relief", "story": "They continue their phone call.",
        "setting": "Koko's bright, minimal desk area",
        # Deliberately reproduce the bad local-model plan: the remote caller is
        # placed beside Koko and listed as physically visible.
        "action": ("Koko holds her smartphone and smiles at the screen. "
                   "Besi stands beside her and says he read the card three times."),
        "ending": "Koko looks at the phone.", "duration": 5,
        "duration_reason": "one remote reaction", "image_prompt": "Koko on a phone call",
        "dialogue": [{"speaker": "Besi", "text": "I read it three times.",
                      "language": "English", "voiceover": False}],
        "card_selection": {"characters": ["Koko", "Besi"]},
        "cast_timeline": {"visible_start": ["Koko", "Besi"],
                          "visible_end": ["Koko", "Besi"], "enters": [], "exits": [],
                          "offscreen": [], "mentioned_only": []},
        "transition_mode": "continuous",
    }], "local_ai")

    roles = character_presence_roles(production, production["segments"][0])
    assert roles[koko["id"]] == "physical"
    assert roles[besi["id"]] == "offscreen"
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    names = {subject["id"]: subject["name"] for subject in project["subjects"]}
    scene = project["shots"][0]
    assert [names[ident] for ident in scene["visible_subject_ids"]] == ["Koko"]
    assert [names[ident] for ident in scene["offscreen_subject_ids"]] == ["Besi"]
    assert "FINAL PRODUCTION RENDER OVERRIDE" in project["production_render_override"]
    compiled = compile_project(project)
    assert compiled["valid"], compiled["issues"]
    assert "production_render_override:" in compiled["prompt"]
    assert "Visible at opening: Koko" in compiled["prompt"]
    assert "Off-screen for the entire clip: Besi" in compiled["prompt"]

    # Accepting the rebuilt prompt must migrate the repaired timeline back to
    # the production clip.  Without this write-back, normalisation would see
    # the planner's old co-located cast again and immediately mark the clip
    # stale, causing one-click production to stop in a repair loop.
    production = manager.set_segment_prompt(
        production["id"], production["segments"][0]["id"], compiled["prompt"],
        "compiled", 1.0, project["id"])
    segment = production["segments"][0]
    assert segment["status"] == "ready", segment.get("stale_reasons")
    assert segment["stale_reasons"] == []
    assert segment["cast_timeline"]["visible_start"] == ["Koko"]
    assert segment["cast_timeline"]["visible_end"] == ["Koko"]
    assert segment["cast_timeline"]["offscreen"] == ["Besi"]


def test_display_only_split_call_keeps_an_explicit_empty_physical_timeline(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    koko = _card("Koko", [_image(store_asset, "Koko", "pink")["id"]])
    besi = _card("Besi", [_image(store_asset, "Besi", "blue")["id"]])
    production = manager.create({
        "source_project": source, "language": "en",
        "brief": ("Koko and Besi can only meet online. They remain physically separate "
                  "and never share the same room."),
    })
    production["cards"]["characters"] = [koko, besi]
    manager.save(production)
    production = manager.apply_plan(production["id"], [{
        "title": "Shared silence",
        "story": "Koko and Besi share a quiet moment over a video call.",
        "setting": "A stable split screen between Koko's room and Besi's room.",
        "action": ("Split screen: Koko smiles at her display in her room while Besi "
                   "leans back and smiles from his separate video panel."),
        "ending": "Both remain inside their separate bounded panels.",
        "duration": 5, "duration_reason": "one remote beat",
        "image_prompt": "Stable split-screen video call between two separate rooms",
        "dialogue": [
            {"speaker": "Koko", "text": "Stay with me.", "language": "English", "voiceover": False},
            {"speaker": "Besi", "text": "I'm here.", "language": "English", "voiceover": False},
        ],
        "card_selection": {"characters": ["Koko", "Besi"]},
        # Reproduce the planner's unsafe legacy interpretation. Both people are
        # visible in the composition, but neither is a body in one shared room.
        "cast_timeline": {"visible_start": ["Koko", "Besi"],
                          "visible_end": ["Koko", "Besi"], "enters": [], "exits": [],
                          "offscreen": [], "mentioned_only": []},
        "transition_mode": "hard_cut",
    }], "local_ai")

    roles = character_presence_roles(production, production["segments"][0])
    assert roles[koko["id"]] == "display"
    assert roles[besi["id"]] == "display"
    materialised = manager.materialise(production["id"], production["segments"][0]["id"])
    project = materialised["project"]
    scene = project["shots"][0]
    assert scene["visible_subject_ids"] == []
    assert len(scene["display_subject_ids"]) == 2
    compiled = compile_project(project)
    assert compiled["valid"], compiled["issues"]

    production = manager.set_segment_prompt(
        production["id"], production["segments"][0]["id"], compiled["prompt"],
        "compiled", 1.0, project["id"])
    segment = production["segments"][0]
    assert segment["status"] == "ready", segment.get("stale_reasons")
    assert segment["stale_reasons"] == []
    assert not any(segment["cast_timeline"].values())


def test_negative_split_screen_lock_does_not_turn_local_actor_into_display(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    besi = _card("Besi")
    koko = _card("Koko")
    production = manager.create({
        "source_project": source, "language": "en",
        "brief": ("Besi receives Koko's card photograph while they remain physically separate "
                  "and never share the same room."),
    })
    production["cards"]["characters"] = [besi, koko]
    segment = normalise_segment({
        "title": "Card photograph", "story": "Besi studies Koko's card photograph.",
        "setting": "Besi's room beside an open laptop.",
        "action": "Besi tilts his head and smiles while Koko's laughter is heard through the phone.",
        "ending": "Besi keeps smiling.", "duration": 5, "duration_reason": "final reaction",
        "dialogue": [],
        "image_prompt": ("Besi is visible behind the laptop showing one digital photograph of Koko's cream "
                         "handwritten card. No split screen and no second room."),
        "card_selection": {"characters": ["Besi"], "wardrobe": [], "props": [],
                           "environments": [], "voices": []},
        "cast_timeline": {"visible_start": ["Besi"], "visible_end": ["Besi"],
                          "enters": [], "exits": [], "offscreen": ["Koko"],
                          "mentioned_only": []},
        "transition_mode": "insert", "device_view": "screen",
    }, 1)

    roles = character_presence_roles(production, segment)

    assert roles[besi["id"]] == "physical"
    assert roles[koko["id"]] == "offscreen"


@pytest.mark.parametrize(("action", "image_prompt", "expected", "rejected"), [
    ("Koko looks at the phone and hesitates.", "Medium close-up on Koko's face.",
     "PERFORMANCE-VIEW GEOMETRY LOCK", "SCREEN-VIEW GEOMETRY LOCK"),
    ("Insert shot: the phone screen shows the cream card.", "Over-the-shoulder phone screen.",
     "SCREEN-VIEW GEOMETRY LOCK", "PERFORMANCE-VIEW GEOMETRY LOCK"),
    ("Koko holds the smartphone to her ear.", "Koko listens on the phone.",
     "PHONE-TO-EAR GEOMETRY LOCK", "SCREEN-VIEW GEOMETRY LOCK"),
    ("Koko looks directly into the phone camera and speaks.", "Phone-camera POV.",
     "FRONT-CAMERA GEOMETRY LOCK", "PERFORMANCE-VIEW GEOMETRY LOCK"),
])
def test_phone_composition_chooses_one_physically_possible_screen_view(
        action, image_prompt, expected, rejected):
    lock = device_screen_geometry_lock({
        "story": "A private phone moment.", "setting": "bedroom",
        "action": action, "ending": "Koko pauses.", "image_prompt": image_prompt})

    assert expected in lock
    assert rejected not in lock
    assert "one front screen and one back" in lock
    assert "no transparent, mirrored, floating, detached, rear-facing or double-sided screen" in lock


def test_remote_call_split_uses_separate_location_panels_not_a_phone_inside_a_phone():
    lock = device_screen_geometry_lock({
        "story": "Koko and Besi speak over a video call.",
        "setting": "Split screen: Koko's room and Besi's apartment.",
        "action": "They look toward each other and smile.",
        "ending": "They share a quiet laugh.",
        "image_prompt": "Two remote locations."}, ["Koko", "Besi"])

    assert "REMOTE-CALL PANEL GEOMETRY LOCK" in lock
    assert "never occupy the same room" in lock
    assert "appear again inside a phone" in lock
    assert "SCREEN-VIEW GEOMETRY LOCK" not in lock


def test_separate_room_language_forces_remote_panels_over_saved_performance_view():
    koko, besi = _card("Koko"), _card("Besi")
    production = {
        "brief": "Koko and Besi remain physically separate.",
        "cards": {**{kind: [] for kind in CARD_KINDS}, "characters": [koko, besi]},
    }
    segment = {
        "device_view": "performance",
        "story": "They talk remotely.",
        "setting": "Split locations in their separate rooms.",
        "action": "Alternating matching close-ups: Koko speaks, then Besi answers.",
        "ending": "Each remains in a separate room.", "image_prompt": "Remote conversation",
        "card_selection": {"characters": ["Koko", "Besi"]},
        "cast_timeline": {"visible_start": ["Koko", "Besi"],
                          "visible_end": ["Koko", "Besi"], "enters": [], "exits": [],
                          "offscreen": [], "mentioned_only": []},
        "dialogue": [],
    }

    roles = character_presence_roles(production, segment)
    assert roles == {koko["id"]: "display", besi["id"]: "display"}
    lock = device_screen_geometry_lock(segment, ["Koko", "Besi"])
    assert "REMOTE-CALL PANEL GEOMETRY LOCK" in lock
    assert "PHYSICAL GEOGRAPHY OVERRIDE" in lock
    assert "PERFORMANCE-VIEW GEOMETRY LOCK" not in lock


def test_laptop_only_scene_uses_static_display_instead_of_phone_geometry():
    lock = device_screen_geometry_lock({
        "story": "Besi reads a message on his laptop.",
        "setting": "Besi's desk and computer monitor.",
        "action": "The laptop display shows Koko's photograph while Besi reacts.",
        "ending": "The photograph remains inside the laptop bezel.",
        "image_prompt": "Laptop insert at a desk.",
    }, ["Koko"])

    assert "STATIC-DISPLAY GEOMETRY LOCK" in lock
    assert "Do not invent a phone" in lock
    assert "SCREEN-VIEW GEOMETRY LOCK" not in lock
    assert "full-size physical copy" in lock


def test_phone_screen_lock_forbids_two_copies_of_the_holder():
    lock = device_screen_geometry_lock({
        "story": "Koko checks a phone photograph.", "setting": "desk",
        "action": "Over-the-shoulder insert of the phone screen.",
        "ending": "Koko lowers the phone.", "image_prompt": "Phone screen insert",
    })

    assert "SCREEN-VIEW GEOMETRY LOCK" in lock
    assert "two copies of the same identity" in lock


def test_manual_device_view_overrides_conflicting_automatic_phone_framing():
    segment = {
        "device_view": "performance",
        "story": "Koko receives Besi's video call.", "setting": "Koko's room",
        "action": "Besi's face appears on the phone screen while Koko reacts.",
        "ending": "Koko smiles.", "image_prompt": "Koko holding a phone."}

    lock = device_screen_geometry_lock(segment, ["Besi"])

    assert "PERFORMANCE-VIEW GEOMETRY LOCK" in lock
    assert "SCREEN-VIEW GEOMETRY LOCK" not in lock
    assert "DIRECTOR OVERRIDE" in lock


def test_mirror_lock_keeps_remote_and_absent_people_out_of_the_reflection():
    koko, besi = _card("Koko"), _card("Besi")
    production = {"brief": "Koko misses Besi.", "cards": {"characters": [koko, besi]}}
    segment = {
        "story": "Koko misses Besi while standing at the mirror.",
        "setting": "Koko's bedroom mirror",
        "action": "Koko looks at her own reflection while Besi appears only on the phone screen.",
        "ending": "Koko lowers the phone.", "image_prompt": "Koko reflected in the mirror",
        "card_selection": {"characters": ["Koko", "Besi"]},
        "cast_timeline": {"visible_start": ["Koko"], "visible_end": ["Koko"],
                          "enters": [], "exits": [], "offscreen": [],
                          "mentioned_only": []},
    }

    lock = effective_temporal_cast_lock(
        production, segment, {koko["id"]: "physical", besi["id"]: "display"})

    assert "MIRROR GEOMETRY LOCK" in lock
    assert "A reflection is not another physical body" in lock
    assert "Display-only, imagined, off-screen and absent identities never appear in the mirror" in lock


def test_written_prop_lock_preserves_one_authored_card_and_defers_exact_type():
    lock = inscribed_prop_continuity_lock([{
        "name": "Cream confession card",
        "description": "A folded cream card handwritten with the authored confession.",
        "notes": "Keep the ink and fold unchanged.",
    }])

    assert "INSCRIBED-PROP CONTINUITY LOCK" in lock
    assert "Cream confession card" in lock
    assert "never invent, paraphrase, translate, extend or mutate its text" in lock
    assert "typography composited in post" in lock


def test_segment_device_view_defaults_to_auto_and_invalid_legacy_values_are_safe():
    base = _planned_clip("Phone beat", [], {key: [] for key in
                         ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")})

    assert normalise_segment(base, 0)["device_view"] == "auto"
    base["device_view"] = "transparent_magic_phone"
    assert normalise_segment(base, 0)["device_view"] == "auto"
    base["device_view"] = "screen"
    assert normalise_segment(base, 0)["device_view"] == "screen"


def test_video_admission_rejects_old_or_missing_character_identity_strategy(tmp_path):
    manager, _source, _projects, _assets, _store_asset = _rig(tmp_path)
    production_id, segment_id, project_id = _id(), _id(), _id()
    production = {"id": production_id, "segments": [{
        "id": segment_id, "project_id": project_id, "status": "ready",
        "reference_strategy_version": REFERENCE_STRATEGY_VERSION}]}
    project = {"id": project_id, "production_link": {
        "production_id": production_id, "segment_id": segment_id,
        "reference_strategy": {"reference_strategy_version": REFERENCE_STRATEGY_VERSION,
                               "missing_visual_identity_names": ["Heron Mask"]}}}

    with pytest.raises(ValueError, match="Heron Mask"):
        manager.assert_video_project_current(production, project)
    project["production_link"]["reference_strategy"]["missing_visual_identity_names"] = []
    manager.assert_video_project_current(production, project)
    project["production_link"]["reference_strategy"]["reference_strategy_version"] -= 1
    with pytest.raises(ValueError, match="outdated character-reference assignment"):
        manager.assert_video_project_current(production, project)


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


def test_shot_timecode_body_excludes_next_part_metadata():
    screenplay = """## Part 2: Sent Across the Distance

**Time:** 00:10–00:20
**Characters appearing:** Koko

### Shot 1 | 00:10–00:15
Koko photographs the card.

### Shot 2 | 00:15–00:20
Koko sends the photograph and turns the phone face down.

---

## Part 3: The Old Mood

**Time:** 00:20–00:30
**Duration:** 10s
**Characters appearing:** Besi

### Shot 1 | 00:20–00:30
Besi enters his apartment and notices the phone.

---

## Production Continuity Notes

The physical card remains in Koko's room.
"""

    beats = timed_story_beats(screenplay)

    assert [(beat["start"], beat["end"]) for beat in beats] == [
        (10, 15), (15, 20), (20, 30)]
    assert "Part 3" not in beats[1]["text"]
    assert "Duration" not in beats[1]["text"]
    assert "Characters appearing" not in beats[1]["text"]
    assert beats[1]["text"] == "Koko sends the photograph and turns the phone face down."
    assert beats[2]["text"] == "Besi enters his apartment and notices the phone."
    assert "Production Continuity Notes" not in beats[2]["text"]


def test_long_timed_fallback_keeps_render_fields_inside_segment_schema():
    screenplay = "0:00-0:10\n" + ("A crosses the room and records every visible production detail. " * 90)

    clips = fallback_segments(screenplay, 10, "en")
    segment = normalise_segment(clips[0], 0)

    assert len(segment["story"]) <= 3000
    assert len(segment["action"]) <= 3000
    assert len(segment["image_prompt"]) <= 3000
    assert "…" in segment["story"]


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


def test_duration_fitting_gives_dense_exact_dialogue_more_of_fixed_episode_budget():
    long_line = " ".join(f"word{index}" for index in range(32))
    clips = [
        {"duration": 10, "action": "A delivers the report.",
         "dialogue": [{"speaker": "A", "text": long_line}]},
        {"duration": 10, "action": "B crosses the room.", "dialogue": []},
        {"duration": 10, "action": "The light fades.", "dialogue": []},
    ]

    fitted = fit_planned_durations(clips, 30, "en")

    assert sum(item["duration"] for item in fitted) == 30
    assert fitted[0]["duration"] == 15
    assert all(5 <= item["duration"] <= 15 for item in fitted)


def test_dialogue_overrun_keeps_ai_storyboard_and_relaxes_episode_target():
    clips = [{
        "title": f"Distinct beat {index}", "duration": 10,
        "action": f"Character {index} completes a distinct action.",
        "duration_reason": "AI planned dramatic beat",
        "dialogue": [{"speaker": "A", "text": " ".join(
            f"word{word}" for word in range(22))}],
    } for index in range(3)]

    fitted = fit_planned_durations(clips, 30, "en")

    assert [item["title"] for item in fitted] == ["Distinct beat 0", "Distinct beat 1", "Distinct beat 2"]
    assert sum(item["duration"] for item in fitted) == 33
    assert all(item["duration"] == 11 for item in fitted)
    assert "extending generated duration from 30s to 33s" in fitted[0]["duration_reason"]


def test_storyboard_planning_chunks_keep_long_local_answers_bounded():
    story = "\n".join(
        f"Beat {index}: The performers complete a distinct visible action and reach a changed state."
        for index in range(140)
    )

    pieces = storyboard_planning_chunks(story)

    assert len(pieces) >= 5
    assert max(map(len, pieces)) <= 2000
    assert "".join(pieces).replace("\n", "") == story.replace("\n", "")


def test_storyboard_output_budget_never_exceeds_local_client_limit():
    assert storyboard_output_token_budget(1) == 3100
    assert storyboard_output_token_budget(2) == 4096
    assert storyboard_output_token_budget(20) == 4096
    assert storyboard_output_token_budget(0) == 3100


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
    piece = timed_group_story(timed_clip_groups(screenplay)[0])
    timing = episode_timing_targets(production, 1, 3, piece)
    assert timing["part_target_seconds"] == 10
    assert timing["recommended_clip_count"] == 1
    assert timing["timed_clip_groups"] == [{
        "clip": 1, "start_seconds": 0, "end_seconds": 10, "duration_seconds": 10,
    }]
    assert locked_timed_dialogue(production, piece)[0]["source_dialogue"][0]["text"] == (
        "Keep your side clear.")
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
    repaired = manager.apply_plan(production["id"], broken, "local_ai")
    assert repaired["segments"][0]["dialogue"] == []
    assert repaired["segments"][1]["dialogue"][0]["text"] == "Keep your side clear."

    omitted = copy.deepcopy(fitted)
    omitted[0]["dialogue"] = [{"speaker": "Bokka", "text": "Invented.",
                                "language": "English", "voiceover": False}]
    omitted[1]["dialogue"] = []
    repaired = manager.apply_plan(production["id"], omitted, "local_ai")
    assert repaired["segments"][0]["dialogue"] == []
    assert repaired["segments"][1]["dialogue"][0]["text"] == "Keep your side clear."


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


def test_editorial_part_heading_and_written_card_copy_are_not_dialogue():
    screenplay = """00:40-00:50
## Part 5: “I Love You”
**Characters appearing:** Besi
**Speaking voice cards:** None

**Action:** The card reads: “Besi, I love you. — Koko.”
**No dialogue.**"""

    assert script_dialogue(screenplay) == []
    assert locked_timed_dialogue({
        "language": "en", "brief": screenplay, "current_episode": 1, "episodes": [],
        "cards": {"characters": []},
    })[0]["dialogue_parse_failed"] is False
    clip = _planned_clip("Printed message", [{
        "speaker": "Part 5", "text": "I Love You", "language": "English",
        "voiceover": False,
    }], {key: [] for key in
         ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")})
    assert normalise_segment(clip, 0)["dialogue"] == []


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


def test_series_voice_roster_restores_only_the_current_speakers_rules(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({
        "source_project": source, "brief": "Pokke speaks while Nox listens.", "language": "en",
        "series_voice_style": (
            "Animated ensemble dialogue.\n"
            "- Pokke should remain fast and bouncy, never robotic.\n"
            "- Nox should remain smoky and dry, never growling.\n"
            "#### The characters should have clearly differentiated voices:\n"
            "Pokke: bright youthful momentum.\n"
            "Nox: controlled quiet amusement."),
    })
    pokke, nox = _card("Pokke"), _card("Nox")
    pokke_voice = _card("Pokke voice", character_card_id=pokke["id"],
                        description="A quick animated voice.", voice_id="POKKE_V1")
    nox_voice = _card("Nox voice", character_card_id=nox["id"],
                      description="A smoky animated voice.", voice_id="NOX_V1")
    production["cards"].update({"characters": [pokke, nox], "voices": [pokke_voice, nox_voice]})
    manager.save(production)
    production = manager.apply_plan(production["id"], [{
        "title": "Reply", "story": "Pokke answers Nox.", "setting": "hall",
        "action": "Pokke speaks while Nox listens.", "ending": "They wait.",
        "duration": 5, "duration_reason": "one reply", "image_prompt": "Pokke and Nox",
        "dialogue": [{"speaker": "Pokke", "text": "This way!", "language": "English",
                      "voiceover": False}],
        "card_selection": {"characters": ["Pokke", "Nox"]},
    }], "local_ai")
    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    context = project["custom_instructions"]
    assert "Pokke should remain fast and bouncy, never robotic" in context
    assert "Pokke: bright youthful momentum" in context
    assert "Nox should remain smoky" not in context
    assert "Nox: controlled quiet amusement" not in context


def test_mimic_and_recorded_speakers_reuse_the_selected_voice_authority(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A mirror bird mimics Pokke.",
                                 "language": "en"})
    pokke, bird = _card("Pokke"), _card("Mirror Bird")
    pokke_voice = _card("Pokke voice", character_card_id=pokke["id"],
                        description="Fast and bouncy.", voice_id="POKKE_V1")
    production["cards"].update({"characters": [pokke, bird], "voices": [pokke_voice]})
    manager.save(production)
    clips = [{
        "title": "Exact mimic", "story": "The Mirror Bird mimics Pokke's exact voice.",
        "setting": "mirror hall", "action": "Pokke speaks, then the bird repeats the same words.",
        "ending": "The bird tilts its head.", "duration": 6, "duration_reason": "one echo",
        "image_prompt": "Pokke and the Mirror Bird",
        "dialogue": [
            {"speaker": "Pokke", "text": "That is mine.", "language": "English", "voiceover": False},
            {"speaker": "Mirror Bird", "text": "That is mine.", "language": "English", "voiceover": False},
        ],
        "card_selection": {"characters": ["Pokke", "Mirror Bird"], "voices": ["Pokke voice"]},
    }, {
        "title": "Recorded reflection", "story": "A recorded reflection plays Pokke's voice.",
        "setting": "mirror hall", "action": "A reflection recording says the saved line.",
        "ending": "The reflection fades.", "duration": 5, "duration_reason": "one playback",
        "image_prompt": "a bounded mirror recording",
        "dialogue": [{"speaker": "Pokke reflection", "text": "Wait here.",
                      "language": "English", "voiceover": False}],
        "card_selection": {"characters": [], "voices": ["Pokke voice"]},
    }]
    production = manager.apply_plan(production["id"], clips, "local_ai")
    first = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    first_delivery = [line["delivery"] for line in first["shots"][0]["dialogue"]]
    assert all("Pokke voice" in delivery and "POKKE_V1" in delivery
               for delivery in first_delivery)
    production = manager.get(production["id"])
    second = manager.materialise(production["id"], production["segments"][1]["id"])["project"]
    assert "Pokke voice" in second["shots"][0]["dialogue"][0]["delivery"]
    assert "POKKE_V1" in second["shots"][0]["dialogue"][0]["delivery"]


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
    assert p["narrative_voice"]["cards"][0]["voice_id"] == "POKKE"
    assert "Pokke: brisk" in p["narrative_voice"]["cards"][0]["series_rules"]
    assert len(p["narrative_voice"]["cards"]) == 1
    assert "VOICE DIRECTION" not in p["custom_instructions"]
    assert not p["h3_verbatim_blocks"]
    compiled = compile_project(p)
    assert compiled["valid"], compiled["issues"]
    text = compiled["prompt"]
    assert text.startswith("asset_roles:")
    assert "Brisk and bouncy" in text and "Smooth and smoky" not in text
    assert "Stable recurring voice identity key: POKKE" in text
    assert "Pokke: brisk" in text
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


def test_voice_id_speaker_resolves_to_owner_without_creating_visible_duplicate(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    ree = _card("Ree", [_image(store_asset, "Ree", "purple")["id"]])
    voice = _card("Ree voice", character_card_id=ree["id"], voice_id="REE_V1",
                  language="en", pace="clipped")
    ree["voice_card_id"] = voice["id"]
    production = manager.create({"source_project": source, "brief": "Ree reports an alert."})
    production["cards"]["characters"] = [ree]
    production["cards"]["voices"] = [voice]
    production = manager.save(production)
    planned = [{
        "title": "Alert", "story": "Ree reports an alert.", "setting": "plaza",
        "action": "Ree raises one hand.", "ending": "Ree remains visible.",
        "duration": 5, "duration_reason": "one short line", "image_prompt": "Ree in the plaza",
        "dialogue": [{"speaker": "REE_V1", "text": "Incoming beacon.",
                      "language": "English", "voiceover": False}],
        "card_selection": {"characters": ["Ree"], "voices": ["Ree voice"]},
    }]

    production = manager.apply_plan(production["id"], planned, "local_ai")
    segment = production["segments"][0]
    assert segment["dialogue"][0]["speaker"] == "Ree"

    # Old saved productions can still contain the voice ID. Rebuilding them
    # must repair the detached render project without changing the card library.
    segment["dialogue"][0]["speaker"] = "REE_V1"
    production = manager.save(production)
    project = manager.materialise(production["id"], segment["id"])["project"]
    assert [subject["name"] for subject in project["subjects"]] == ["Ree"]
    scene = project["shots"][0]
    assert scene["visible_subject_ids"] == [project["subjects"][0]["id"]]
    assert scene["dialogue"][0]["speaker_id"] == project["subjects"][0]["id"]
    assert all(subject["description"] != "Production dialogue speaker; add identity references if visible."
               for subject in project["subjects"])


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


def test_revised_prompt_releases_old_manually_selected_take(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "One short scene."})
    production = manager.apply_plan(production["id"], [{
        "title": "Beat", "story": "A waits.", "setting": "room", "action": "A waits.",
        "ending": "A looks up.", "duration": 5, "duration_reason": "one beat",
        "image_prompt": "A waiting", "dialogue": [], "card_selection": {},
    }], "local_ai")
    segment = production["segments"][0]
    segment["selected_video_run_id"] = _id()
    production = manager.save(production)

    updated = manager.set_segment_prompt(
        production["id"], segment["id"], "current prompt", "compiled", 1.2)

    assert updated["segments"][0]["selected_video_run_id"] is None
    assert updated["segments"][0]["video_prompt"] == "current prompt"


def test_post_render_repair_requires_explicit_calls_and_stops_after_three(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "title": "Quality review"})
    production = manager.apply_plan(production["id"], [{
        "title": "One careful action", "story": "A waits.", "setting": "room",
        "action": "A waits.", "ending": "A looks up.", "duration": 5,
        "duration_reason": "one beat", "image_prompt": "A waiting",
        "dialogue": [], "card_selection": {},
    }], "local_ai")
    segment_id = production["segments"][0]["id"]

    for attempt in range(1, 4):
        production = manager.apply_quality_repair(
            production["id"], segment_id,
            "Keep exactly one physical instance of the visible character.")
        segment = production["segments"][0]
        assert segment["quality_repair_count"] == attempt
        assert segment["status"] == "stale"
        assert not segment["video_prompt"]

    with pytest.raises(ValueError, match="three approved quality-repair attempts"):
        manager.apply_quality_repair(
            production["id"], segment_id,
            "Keep exactly one physical instance of the visible character.")


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


def test_text_only_clip_falls_back_from_ref2va_without_using_style_image_as_subject(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    style_image = _image(store_asset, "watercolor-style", "blue")
    production = manager.create({
        "source_project": source, "brief": "Hero waits in the rain.", "source_mode": "ref2va",
        "video_quality": "lora8",
    })
    production["cards"]["characters"] = [_card("Hero")]
    production["cards"]["styles"] = [
        _card("Watercolor", [style_image["id"]], image_analysis="Loose watercolor pigment on paper.")]
    production = manager.save(production)
    timeline = {"visible_start": ["Hero"], "visible_end": ["Hero"], "enters": [], "exits": [],
                "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(
        production["id"], [_planned_clip("Hero waits", ["Hero"], timeline)], "local_ai")

    result = manager.materialise(production["id"], production["segments"][0]["id"])
    project = result["project"]

    assert project["mode"] == "t2va"
    assert project["comfy_render"]["workflow_profile_id"] == "builtin"
    assert project["comfy_render"]["quality"] == "fast"
    assert project["production_link"]["reference_strategy"]["effective_mode"] == "t2va"
    assert project["production_link"]["reference_strategy"]["missing_visual_identity_names"] == ["Hero"]
    assert "TEXT-ONLY VISIBLE IDENTITY WARNING" in project["production_planning_context"]
    assert [(asset["id"], asset["role"]) for asset in project["assets"]] == [
        (style_image["id"], "context")]
    compiled = compile_project(project)
    assert compiled["valid"] is True, compiled["issues"]


def test_text_only_source_promotes_clip_to_ref2va_when_selected_card_has_an_image(tmp_path):
    manager, source, projects, _assets, store_asset = _rig(tmp_path)
    source["mode"] = "t2va"
    projects[source["id"]] = copy.deepcopy(source)
    hero_image = _image(store_asset, "hero", "green")
    production = manager.create({
        "source_project": source, "brief": "Hero enters.", "source_mode": "t2va",
    })
    production["cards"]["characters"] = [_card("Hero", [hero_image["id"]])]
    production = manager.save(production)
    timeline = {"visible_start": [], "visible_end": ["Hero"], "enters": ["Hero"], "exits": [],
                "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(
        production["id"], [_planned_clip("Hero enters", ["Hero"], timeline)], "local_ai")

    project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]

    assert project["mode"] == "ref2va"
    assert project["production_link"]["reference_strategy"]["effective_mode"] == "ref2va"
    assert project["production_link"]["reference_strategy"]["missing_visual_identity_names"] == []
    assert [asset["id"] for asset in project["assets"] if asset.get("role") == "reference_image"] == [
        hero_image["id"]]
    compiled = compile_project(project)
    assert compiled["valid"] is True, compiled["issues"]


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


def test_old_prepared_reference_strategy_keeps_adopted_take_ready(tmp_path):
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
    assert checked["segments"][0]["status"] == "ready"
    assert checked["segments"][0]["stale_reasons"] == []


def test_old_device_geometry_prompt_marks_only_that_clip_for_rebuild(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A checks a phone, then waits."})
    character = _card("A", [_image(store_asset, "a", "red")["id"]])
    production["cards"]["characters"] = [character]
    production = manager.save(production)
    timeline = {"visible_start": ["A"], "visible_end": ["A"], "enters": [],
                "exits": [], "offscreen": [], "mentioned_only": []}
    phone = _planned_clip("A checks the phone", ["A"], timeline)
    phone["action"] = "Insert shot: A checks the phone screen."
    plain = _planned_clip("A waits", ["A"], timeline)
    production = manager.apply_plan(production["id"], [phone, plain], "local_ai")
    for row in list(production["segments"]):
        result = manager.materialise(production["id"], row["id"])
        project = result["project"]
        prompt = compile_project(project)["prompt"]
        production = manager.set_segment_prompt(
            production["id"], row["id"], prompt, "compiled", 1.0, project["id"])

    production["segments"][0]["video_prompt"] = production["segments"][0]["video_prompt"].replace(
        "DEVICE GEOMETRY CONTRACT V2. ", "")
    checked = manager.save(production)

    assert checked["segments"][0]["status"] == "stale"
    assert any("旧版设备构图规则" in reason
               for reason in checked["segments"][0]["stale_reasons"])
    assert checked["segments"][1]["status"] == "ready"
    assert checked["segments"][1]["stale_reasons"] == []


def test_generated_card_image_marks_only_clips_using_that_card_stale(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "Hero waits. Friend enters."})
    hero = _card("Hero")
    friend = _card("Friend", [_image(store_asset, "friend", "blue")["id"]])
    production["cards"]["characters"] = [hero, friend]
    production = manager.save(production)
    timeline = lambda name: {"visible_start": [name], "visible_end": [name], "enters": [],
                             "exits": [], "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(production["id"], [
        _planned_clip("Hero waits", ["Hero"], timeline("Hero")),
        _planned_clip("Friend enters", ["Friend"], timeline("Friend")),
    ], "local_ai")
    for segment in list(production["segments"]):
        production = manager.materialise(production["id"], segment["id"])["production"]

    generated = _image(store_asset, "hero-generated", "red")
    changed = manager.attach_generated_card_asset(
        production["id"], "characters", hero["id"], generated)["production"]

    assert changed["segments"][0]["status"] == "stale"
    assert any("全片约束或资产卡已变化" in reason
               for reason in changed["segments"][0]["stale_reasons"])
    assert changed["segments"][1]["status"] == "ready"
    assert changed["segments"][1]["stale_reasons"] == []


def test_identity_repair_marks_only_the_affected_saved_clip_stale(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    hero = _card("Hero", [_image(store_asset, "hero", "red")["id"]])
    friend = _card("Friend", [_image(store_asset, "friend", "blue")["id"]])
    voice = _card("Hero voice", character_card_id=hero["id"], voice_id="HERO_V1",
                  language="en", pace="steady")
    hero["voice_card_id"] = voice["id"]
    production = manager.create({"source_project": source, "brief": "Hero speaks, then Friend waits."})
    production["cards"]["characters"] = [hero, friend]
    production["cards"]["voices"] = [voice]
    production = manager.save(production)
    hero_timeline = {"visible_start": ["Hero"], "visible_end": ["Hero"], "enters": [],
                     "exits": [], "offscreen": [], "mentioned_only": []}
    friend_timeline = {"visible_start": ["Friend"], "visible_end": ["Friend"], "enters": [],
                       "exits": [], "offscreen": [], "mentioned_only": []}
    first = _planned_clip("Hero reports", ["Hero"], hero_timeline)
    first["dialogue"] = [{"speaker": "Hero", "text": "Ready.",
                           "language": "English", "voiceover": False}]
    production = manager.apply_plan(production["id"], [
        first, _planned_clip("Friend waits", ["Friend"], friend_timeline),
    ], "local_ai")
    for segment in list(production["segments"]):
        production = manager.materialise(production["id"], segment["id"])["production"]

    production["segments"][0]["dialogue"][0]["speaker"] = "HERO_V1"
    production["segments"][0]["cast_timeline"] = {
        "visible_start": [], "visible_end": [], "enters": [], "exits": [],
        "offscreen": ["Friend"], "mentioned_only": []}
    production["segments"][0]["source_hash"] = segment_hash(production["segments"][0])
    checked = manager.save(production)

    assert checked["segments"][0]["status"] == "stale"
    assert any("声线卡 ID" in reason for reason in checked["segments"][0]["stale_reasons"])
    assert any("角色时间线" in reason for reason in checked["segments"][0]["stale_reasons"])
    assert checked["segments"][1]["status"] == "ready"
    assert checked["segments"][1]["stale_reasons"] == []

    repaired = manager.materialise(checked["id"], checked["segments"][0]["id"])["production"]
    repaired_first = repaired["segments"][0]
    assert repaired_first["dialogue"][0]["speaker"] == "Hero"
    assert repaired_first["cast_timeline"]["visible_start"] == ["Hero"]
    assert repaired_first["cast_timeline"]["visible_end"] == ["Hero"]
    assert repaired_first["status"] == "ready"
    assert repaired["segments"][1]["status"] == "ready"


def test_voice_card_with_canonical_character_name_does_not_mark_clip_stale(tmp_path):
    manager, source, _projects, _assets, store_asset = _rig(tmp_path)
    hero = _card("Mimi", [_image(store_asset, "mimi", "red")["id"]])
    # Real card libraries commonly give the voice card the same display name
    # as its character while keeping a distinct synthesis ID.
    voice = _card("Mimi", character_card_id=hero["id"], voice_id="Mimi_Voice",
                  language="en", pace="steady")
    hero["voice_card_id"] = voice["id"]
    production = manager.create({"source_project": source, "brief": "Mimi speaks."})
    production["cards"]["characters"] = [hero]
    production["cards"]["voices"] = [voice]
    production = manager.save(production)
    timeline = {"visible_start": ["Mimi"], "visible_end": ["Mimi"], "enters": [],
                "exits": [], "offscreen": [], "mentioned_only": []}
    clip = _planned_clip("Mimi reports", ["Mimi"], timeline)
    clip["dialogue"] = [{"speaker": "Mimi", "text": "Ready.",
                         "language": "English", "voiceover": False}]
    production = manager.apply_plan(production["id"], [clip], "local_ai")

    prepared = manager.materialise(
        production["id"], production["segments"][0]["id"])["production"]

    assert prepared["segments"][0]["status"] == "ready"
    assert prepared["segments"][0]["stale_reasons"] == []


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


def test_stale_long_running_save_cannot_clear_an_explicit_pause(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A crosses the library."})
    stale_active_snapshot = copy.deepcopy(production)

    paused = manager.update(production["id"], {"task_state": "paused"})
    assert paused["task_state"] == "paused"

    stale_active_snapshot["style_bible"] = "A late planning result."
    saved = manager.save(stale_active_snapshot)
    assert saved["task_state"] == "paused"
    assert manager.get(production["id"])["task_state"] == "paused"

    resumed = manager.update(production["id"], {"task_state": "active"})
    assert resumed["task_state"] == "active"


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


def test_temporal_cast_guard_restores_visible_speaker_as_implicit_reentry(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    hero, guard = _card("Hero"), _card("Guard")
    guard_voice = _card("Guard voice", character_card_id=guard["id"], voice_id="GUARD_V1",
                        language="en", pace="steady")
    guard["voice_card_id"] = guard_voice["id"]
    production = manager.create({"source_project": source, "brief": "Guard leaves, then speaks after returning."})
    production["cards"]["characters"] = [hero, guard]
    production["cards"]["voices"] = [guard_voice]
    production = manager.save(production)
    empty = {key: [] for key in
             ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")}
    departure = copy.deepcopy(empty)
    departure.update(visible_start=["Hero", "Guard"], visible_end=["Hero"], exits=["Guard"])
    mistaken_return = copy.deepcopy(empty)
    mistaken_return.update(visible_start=["Hero", "Guard"], visible_end=["Hero", "Guard"])
    second = _planned_clip("Guard returns and reports", ["Hero", "Guard"], mistaken_return)
    second["dialogue"] = [{"speaker": "GUARD_V1", "text": "I am back.",
                           "language": "English", "voiceover": False}]

    production = manager.apply_plan(production["id"], [
        _planned_clip("Guard departs the story", ["Hero", "Guard"], departure, "state_change"),
        second,
    ], "local_ai")

    returned = production["segments"][1]
    assert returned["dialogue"][0]["speaker"] == "Guard"
    assert returned["cast_timeline"]["enters"] == ["Guard"]
    assert returned["cast_timeline"]["visible_end"] == ["Hero", "Guard"]
    assert any("implicit re-entry" in item for item in returned["continuity_warnings"])
    project = manager.materialise(production["id"], returned["id"])["project"]
    assert "FINAL-FRAME CAST LOCK: show exactly Hero, Guard" in project["shots"][0]["final_state"]


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


def _continuity_state(opening, ending, names, *, mmh3):
    positions = [{"character": name, "position": "left" if index == 0 else "right",
                  "facing": "toward partner", "movement_direction": "still",
                  "eyeline_target": names[1 - index] if len(names) == 2 else "camera",
                  "eyeline_direction": "right" if index == 0 else "left"}
                 for index, name in enumerate(names)]
    return {"opening_state": opening, "ending_state": ending,
            "positions_start": copy.deepcopy(positions), "positions_end": copy.deepcopy(positions),
            "prop_holders_start": [], "prop_holders_end": [], "mmh3_eligible": mmh3}


def _shot_contract(relation, role="master"):
    return {"role": role, "shot_size": "medium", "opening_composition": "A left, B right",
            "ending_composition": "A left, B right", "camera_axis": "A-B conversation axis",
            "allow_axis_cross": False, "edit_reason": "dialogue_reaction",
            "relation_previous": relation, "preserve_from_previous": "positions and card holder",
            "must_change": "speaker reaction"}


def test_new_planner_rejects_missing_source_reference(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A opens the door."})
    production = manager.save(production)
    manifest = production_source_manifest(production)
    paragraph = manifest["chunks"][0]["paragraphs"][0]
    clip = _planned_clip("A opens the door", [], {key: [] for key in
        ("visible_start", "visible_end", "enters", "exits", "offscreen", "mentioned_only")}, "hard_cut")
    clip["source_refs"] = {"scene_ids": [paragraph["scene_id"]], "paragraph_ids": [paragraph["id"]],
                           "dialogue_ids": [], "event_ids": []}
    clip["continuity_state"] = _continuity_state("closed door", "open door", [], mmh3=False)
    clip["shot_contract"] = _shot_contract("hard_cut")
    with pytest.raises(ValueError, match="missing event_ids"):
        manager.apply_plan(production["id"], [clip], "local_ai")


def test_each_local_story_part_is_rejected_immediately_when_its_tail_is_missing():
    story = "Scene\nA opens the door.\nA crosses the room.\nA closes the window."
    manifest = production_source_manifest({"brief": story, "episode_count": 1,
                                           "episode_minutes": .5, "episodes": [],
                                           "current_episode": 1})["chunks"][0]
    clip = _planned_clip("A opens the door", [], {
        "visible_start": [], "visible_end": [], "enters": [], "exits": [],
        "offscreen": [], "mentioned_only": []}, "hard_cut")
    clip["source_refs"] = {
        "scene_ids": [manifest["scenes"][0]["id"]],
        "paragraph_ids": [manifest["paragraphs"][0]["id"]],
        "dialogue_ids": [],
        "event_ids": [manifest["events"][0]["id"]],
    }

    with pytest.raises(ValueError, match="Storyboard part 1.*missing paragraph_ids"):
        validate_planned_chunk_source_contract(story, 1, [clip])

    clip["source_refs"] = {
        "scene_ids": [manifest["scenes"][0]["id"]],
        "paragraph_ids": [row["id"] for row in manifest["paragraphs"]],
        "dialogue_ids": [row["id"] for row in manifest["dialogue"]],
        "event_ids": [row["id"] for row in manifest["events"]],
    }
    overlong = copy.deepcopy(clip)
    overlong["story"] = "x" * 3001
    with pytest.raises(ValueError, match="story must be text with at most 3000 characters"):
        validate_planned_chunk_source_contract(story, 1, [overlong])

    validated = validate_planned_chunk_source_contract(story, 1, [clip])
    assert validated[0]["story"] == clip["story"]
    assert validated[0]["source_refs"] == clip["source_refs"]


def test_source_coverage_and_adjacent_edit_contract_gate_video_prompt(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    brief = 'Scene 1\nA: "Hello."\nA gives the card to B.\nB: "Thanks."'
    production = manager.create({"source_project": source, "brief": brief, "language": "en"})
    production["cards"]["characters"] = [_card("A"), _card("B")]
    production["cards"]["wardrobe"] = [_card("Blue coat"), _card("Red coat")]
    production = manager.save(production)
    chunk = production_source_manifest(production)["chunks"][0]
    paragraphs = {row["text"]: row for row in chunk["paragraphs"]}
    dialogue = chunk["dialogue"]
    event = chunk["events"][0]
    scene_id = chunk["scenes"][0]["id"]
    first_timeline = {"visible_start": ["A", "B"], "visible_end": ["A", "B"],
                      "enters": [], "exits": [], "offscreen": [], "mentioned_only": []}
    second_timeline = {"visible_start": ["B"], "visible_end": ["B"],
                       "enters": [], "exits": [], "offscreen": [], "mentioned_only": []}
    first = _planned_clip("A greets and gives the card", ["A", "B"], first_timeline, "hard_cut")
    first["dialogue"] = [{"speaker": "A", "text": "Hello.", "language": "English", "voiceover": False}]
    first["source_refs"] = {"scene_ids": [scene_id],
                            "paragraph_ids": [chunk["paragraphs"][0]["id"],
                                              paragraphs['A: "Hello."']["id"], event["paragraph_id"]],
                            "dialogue_ids": [dialogue[0]["id"]], "event_ids": [event["id"]]}
    first["continuity_state"] = _continuity_state("A and B face each other", "B holds the card", ["A", "B"], mmh3=False)
    first["continuity_state"]["prop_holders_end"] = [
        {"prop": "phone", "holder": "B", "state": "portrait, screen facing B"}]
    first["card_selection"]["wardrobe"] = ["Blue coat"]
    first["shot_contract"] = _shot_contract("hard_cut")
    second = _planned_clip("B answers", ["B"], second_timeline, "continuous")
    second["dialogue"] = [{"speaker": "B", "text": "Thanks.", "language": "English", "voiceover": False}]
    second["source_refs"] = {"scene_ids": [scene_id],
                             "paragraph_ids": [paragraphs['B: "Thanks."']["id"]],
                             "dialogue_ids": [dialogue[1]["id"]], "event_ids": []}
    second["continuity_state"] = _continuity_state("B holds the card", "B lowers the card", ["B"], mmh3=True)
    second["continuity_state"]["prop_holders_start"] = [
        {"prop": "phone", "holder": "B", "state": "landscape, screen facing camera"}]
    second["card_selection"]["wardrobe"] = ["Red coat"]
    second["shot_contract"] = _shot_contract("continuous", "reaction")
    production = manager.apply_plan(production["id"], [first, second], "local_ai")

    assert production["coverage_report"]["status"] == "ok"
    assert any(row["code"] == "sudden_character_disappearance"
               for row in production["segments"][1]["continuity_issues"])
    assert any(row["code"] == "prop_state_jump"
               for row in production["segments"][1]["continuity_issues"])
    assert any(row["code"] == "wardrobe_jump"
               for row in production["segments"][1]["continuity_issues"])
    first_project = manager.materialise(production["id"], production["segments"][0]["id"])["project"]
    compiled = compile_project(first_project)
    assert "SOURCE COVERAGE LOCK" in compiled["prompt"]
    assert "SHOT EDIT CONTRACT" in compiled["prompt"]
    repaired = manager.materialise(
        production["id"], production["segments"][1]["id"])["production"]
    repaired_segment = repaired["segments"][1]
    assert repaired_segment["transition_mode"] == "hard_cut"
    assert repaired_segment["continue_previous"] is False
    assert repaired_segment["continuity_state"]["mmh3_eligible"] is False
    assert repaired_segment["shot_contract"]["relation_previous"] == "hard_cut"
    assert repaired_segment["cast_timeline"] == second_timeline
    assert not [row for row in repaired_segment["preflight_issues"]
                if row["severity"] == "error"]
    assert any("Auto-repaired" in warning
               for warning in repaired_segment["continuity_warnings"])


def test_shot_preflight_unifies_dialogue_camera_and_internal_cut_failures(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A explains the plan."})
    production["cards"]["characters"] = [_card("A")]
    segment = normalise_segment({
        "title": "Overloaded shot", "story": "A explains the plan.", "setting": "Office",
        "action": ("Wide shot. Cut to a close-up. Cut to an overhead view. "
                   "A turns, walks, opens the case, points, sits, stands and crosses the room."),
        "ending": "A faces camera.", "duration": 5, "duration_reason": "test",
        "dialogue": [{"speaker": "A", "text": "This explanation contains far too many spoken words to fit naturally inside a five second shot.",
                      "language": "English", "voiceover": False}],
        "image_prompt": "", "card_selection": {"characters": ["A"], "wardrobe": [], "props": [],
                                                    "environments": [], "voices": []},
        "cast_timeline": {"visible_start": ["A"], "visible_end": ["A"], "enters": [], "exits": [],
                          "offscreen": [], "mentioned_only": []},
        "transition_mode": "hard_cut", "continuity_state": _continuity_state("A stands", "A stands", ["A"], mmh3=False),
        "shot_contract": _shot_contract("hard_cut"),
    }, 0)
    issues = shot_preflight_for_segment(production, segment)
    codes = {row["code"] for row in issues}
    assert {"multiple_internal_cuts", "impossible_camera_change", "dialogue_overflow"} <= codes
    assert any(row["code"] == "dense_action" and row["severity"] == "warning" for row in issues)


def test_shot_preflight_blocks_a_match_cut_hidden_inside_a_sentence():
    production = {"brief": "A remote exchange.",
                  "cards": {kind: [] for kind in CARD_KINDS}}
    segment = normalise_segment({
        "title": "Across two rooms", "story": "Two people react in separate rooms.",
        "setting": "Two apartments",
        "action": "The image moves through a visual match cut from Koko's desk to Besi's laptop.",
        "ending": "Besi reads the message.", "duration": 10,
        "duration_reason": "one exchange", "dialogue": [], "image_prompt": "",
        "transition_mode": "hard_cut",
    }, 1)

    issues = shot_preflight_for_segment(production, segment)
    assert any(row["code"] == "internal_editorial_cut" and row["severity"] == "error"
               for row in issues)


def test_shot_preflight_does_not_count_one_repeated_match_cut_as_three_edits():
    production = {"brief": "A remote exchange.",
                  "cards": {kind: [] for kind in CARD_KINDS}}
    segment = normalise_segment({
        "title": "Connected card",
        "story": "A visual match cut connects Koko's card to Besi's screen.",
        "setting": "A match cut between two rooms.",
        "action": "The image match-cuts from the paper card to its photograph.",
        "ending": "Besi studies the photograph.", "duration": 5,
        "duration_reason": "final visual rhyme", "dialogue": [], "image_prompt": "",
        "transition_mode": "matched_cut",
    }, 26)

    issues = shot_preflight_for_segment(production, segment)

    assert any(row["code"] == "internal_editorial_cut" and row["severity"] == "error"
               for row in issues)
    assert not any(row["code"] == "multiple_internal_cuts" for row in issues)


def test_shot_preflight_allows_one_plain_cut_to_be_restaged_continuously():
    production = {"brief": "Besi notices the state of his room.",
                  "cards": {kind: [] for kind in CARD_KINDS}}
    segment = normalise_segment({
        "title": "Besi's realization",
        "story": "Besi notices the mess and checks the card photo on his phone.",
        "setting": "Besi's apartment",
        "action": ("POV wide shot pans across dishes and laundry. Cut to Besi; his smile settles into a "
                   "focused look before he glances at the phone in his hand."),
        "ending": "Besi studies the phone.", "duration": 5,
        "duration_reason": "brief reaction", "dialogue": [], "image_prompt": "",
        "transition_mode": "hard_cut",
    }, 13)

    issues = shot_preflight_for_segment(production, segment)

    assert any(row["code"] == "internal_cut" and row["severity"] == "warning" for row in issues)
    assert not any(row["code"] == "internal_editorial_cut" for row in issues)


def test_duplicate_cut_fields_are_reconciled_before_preflight():
    # "continuous" in the descriptive contract must not silently enable
    # saved-motion continuation when the operational field requested a cut.
    assert reconcile_transition_contract("hard_cut", "continuous", 1) == "hard_cut"
    # Preserve a more specific, still-discontinuous editorial cut instead of
    # flattening every safe relation to the generic default.
    assert reconcile_transition_contract("hard_cut", "matched_cut", 1) == "matched_cut"
    assert reconcile_transition_contract("hard_cut", "time_jump", 1) == "time_jump"
    # The first clip has no preceding clip regardless of model prose.
    assert reconcile_transition_contract("continuous", "continuous", 0) == "hard_cut"

    segment = normalise_segment({
        "title": "Photograph the card", "story": "Koko photographs the card.",
        "setting": "desk", "action": "Koko raises the phone.", "ending": "photo saved",
        "duration": 5, "duration_reason": "authored timing", "dialogue": [],
        "image_prompt": "", "transition_mode": "hard_cut",
        "shot_contract": {"relation_previous": "continuous"},
    }, 1)
    assert segment["transition_mode"] == "hard_cut"
    assert segment["shot_contract"]["relation_previous"] == "hard_cut"


def test_modest_authored_dialogue_overrun_warns_but_large_overrun_blocks():
    production = {"brief": "A and B exchange two short lines.",
                  "cards": {kind: [] for kind in CARD_KINDS}}
    compact = normalise_segment({
        "title": "Brief exchange", "story": "A and B exchange two short lines.",
        "setting": "room", "action": "They speak without an added pause.", "ending": "hold",
        "duration": 5, "duration_reason": "authored timing",
        "dialogue": [
            {"speaker": "A", "text": "I have to look more handsome now.", "language": "English", "voiceover": False},
            {"speaker": "B", "text": "You already do.", "language": "English", "voiceover": False},
        ], "image_prompt": "", "transition_mode": "hard_cut",
    }, 1)
    compact_issues = shot_preflight_for_segment(production, compact)
    assert any(row["code"] == "tight_dialogue" and row["severity"] == "warning"
               for row in compact_issues)
    assert not any(row["code"] == "dialogue_overflow" for row in compact_issues)

    overloaded = copy.deepcopy(compact)
    overloaded["dialogue"][0]["text"] = " ".join("word" for _ in range(45))
    overloaded_issues = shot_preflight_for_segment(production, overloaded)
    assert any(row["code"] == "dialogue_overflow" and row["severity"] == "error"
               for row in overloaded_issues)


def test_boundary_actions_save_verified_continuation_or_force_hard_cut(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A waits. A continues waiting."})
    production["cards"]["characters"] = [_card("A")]
    production = manager.save(production)
    timeline = {"visible_start": ["A"], "visible_end": ["A"], "enters": [], "exits": [],
                "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(production["id"], [
        _planned_clip("A waits", ["A"], timeline, "hard_cut"),
        _planned_clip("A continues", ["A"], timeline, "hard_cut"),
    ], "local_heuristic")
    first, second = production["segments"]
    run_id, asset_id = _id(), _id()
    saved = manager.apply_boundary_action(
        production["id"], first["id"], second["id"], run_id, "save", asset_id=asset_id)
    assert saved["segments"][0]["ending_continuity_asset_id"] == asset_id
    continued = manager.apply_boundary_action(
        production["id"], first["id"], second["id"], run_id, "use_next",
        asset_id=asset_id, can_continue=True)
    assert continued["auto_continue_previous"] is True
    assert continued["segments"][0]["selected_video_run_id"] == run_id
    assert continued["segments"][1]["transition_mode"] == "continuous"
    cut = manager.apply_boundary_action(
        production["id"], first["id"], second["id"], run_id, "hard_cut")
    assert cut["segments"][1]["continue_previous"] is False
    assert cut["segments"][1]["shot_contract"]["relation_previous"] == "hard_cut"


def test_empty_prop_state_schema_upgrade_does_not_mark_saved_clip_stale(tmp_path):
    manager, source, _projects, _assets, _store_asset = _rig(tmp_path)
    production = manager.create({"source_project": source, "brief": "A checks the phone."})
    production["cards"]["characters"] = [_card("A")]
    production = manager.save(production)
    timeline = {"visible_start": ["A"], "visible_end": ["A"], "enters": [], "exits": [],
                "offscreen": [], "mentioned_only": []}
    production = manager.apply_plan(production["id"], [
        _planned_clip("A checks the phone", ["A"], timeline, "hard_cut")], "local_heuristic")
    segment = production["segments"][0]
    segment["continuity_state"] = _continuity_state("A holds phone", "A holds phone", ["A"], mmh3=False)
    segment["continuity_state"]["prop_holders_start"] = [
        {"prop": "phone", "holder": "A", "state": ""}]
    production = manager.save(production)
    materialised = manager.materialise(production["id"], production["segments"][0]["id"])["production"]
    segment = materialised["segments"][0]

    # Simulate the brief upgrade build which persisted the normalised empty
    # state in its source hash. Current validation rebases it without asking the
    # user to regenerate a prompt or video.
    segment["source_hash"] = _segment_hash_with_empty_prop_state(segment)
    loaded = manager.save(materialised)

    assert loaded["segments"][0]["status"] == "ready"
    assert loaded["segments"][0]["stale_reasons"] == []
    assert loaded["segments"][0]["source_hash"] == segment_hash(loaded["segments"][0])
