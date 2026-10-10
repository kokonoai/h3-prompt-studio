import copy

from backend.dialogue_audio import (normalise_structured_dialogue_directions,
                                    silence_unstructured_speech_directions)
from backend.projects import merge_plan, new_project
from backend.prompt_quality import (repair_request, review_compiled_prompt,
                                    review_generated_plan, with_repair_history)
from backend.productions import normalise_prompt_quality


def fixture():
    project = new_project()
    project["production_language"] = "en"
    project["duration"] = 10
    project["style"]["genre"] = "watercolor storybook"
    scene = project["shots"][0]
    scene["duration"] = 10
    scene["setting"] = "A quiet studio."
    scene["action"] = "Koko writes one note and pauses."
    scene["final_state"] = "Koko holds the completed note."
    scene["director_locks"] = [
        "setting", "final_state", "visible_subject_ids", "display_subject_ids",
        "imagined_subject_ids", "offscreen_subject_ids",
    ]
    return {"language": "en"}, {"index": 1, "duration": 10, "device_view": "auto"}, project


def test_generated_plan_quality_passes_one_locked_filmable_setup():
    production, segment, baseline = fixture()
    report = review_generated_plan(production, segment, baseline, copy.deepcopy(baseline))
    assert report["status"] == "passed"
    assert report["issues"] == []


def test_generated_plan_allows_authored_named_wordless_cue_but_rejects_invented_one():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    baseline["shots"][0]["action"] = "Koko releases one brief sigh and lowers the note."
    candidate = copy.deepcopy(baseline)

    assert review_generated_plan(production, segment, baseline, candidate)["status"] == "passed"

    candidate["shots"][0]["sound"] = "Koko laughs and giggles."
    report = review_generated_plan(production, segment, baseline, candidate)
    assert report["status"] == "failed"
    assert any(row["code"] == "invented_nonverbal_vocal" for row in report["issues"])


def test_generated_plan_requires_spoken_whispers_to_use_structured_dialogue():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["sound"] = "Koko whispers a reply from the doorway."

    report = review_generated_plan(production, segment, baseline, candidate)

    assert report["status"] == "failed"
    assert any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_phone_stage_directions_bind_to_existing_local_and_remote_dialogue():
    production, segment, baseline = fixture()
    local_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    remote_id = "f2ae0b7c-06ed-460d-aac4-bc8b59e97995"
    baseline["subjects"] = [
        {"id": local_id, "name": "Besi", "description": "", "asset_ids": []},
        {"id": remote_id, "name": "Koko", "description": "", "asset_ids": []},
    ]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [local_id]
    shot["offscreen_subject_ids"] = [remote_id]
    shot["dialogue"] = [
        {"id": "e8681c94-8fc4-47c5-a40d-e05ec917ef26", "speaker_id": local_id,
         "text": "Are you there?", "language": "English", "delivery": "quiet",
         "locked": True, "voiceover": False},
        {"id": "afc6d97b-7a1d-4da2-8465-76a3787d3eae", "speaker_id": remote_id,
         "text": "I am here.", "language": "English", "delivery": "muffled",
         "locked": True, "voiceover": True},
    ]
    shot["action"] = ("Besi speaks into the phone, then pauses, maintaining his gaze "
                      "in the reflection while listening to the response.")
    shot["sound"] = "The muffled, intimate sound of a voice coming from the smartphone speaker."

    candidate = normalise_structured_dialogue_directions(baseline)
    report = review_generated_plan(production, segment, baseline, candidate)

    assert "already-tagged phone dialogue" in candidate["shots"][0]["action"]
    assert "already-tagged remote phone dialogue" in candidate["shots"][0]["sound"]
    assert not any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_line_and_possessive_device_voice_bind_to_structured_dialogue():
    production, segment, baseline = fixture()
    local_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    remote_id = "f2ae0b7c-06ed-460d-aac4-bc8b59e97995"
    baseline["subjects"] = [
        {"id": local_id, "name": "Besi", "description": "", "asset_ids": []},
        {"id": remote_id, "name": "Koko", "description": "", "asset_ids": []},
    ]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [local_id]
    shot["offscreen_subject_ids"] = [remote_id]
    shot["dialogue"] = [
        {"id": "e8681c94-8fc4-47c5-a40d-e05ec917ef26", "speaker_id": local_id,
         "text": "I remember.", "language": "English", "delivery": "quiet",
         "locked": True, "voiceover": False},
        {"id": "afc6d97b-7a1d-4da2-8465-76a3787d3eae", "speaker_id": remote_id,
         "text": "So do I.", "language": "English", "delivery": "muffled",
         "locked": True, "voiceover": True},
    ]
    shot["action"] = "Besi speaks his line while looking at his reflection."
    shot["sound"] = ("Koko's voice is heard through the device speaker, followed by "
                     "one authored soft laugh from Koko.")

    candidate = normalise_structured_dialogue_directions(baseline)
    report = review_generated_plan(production, segment, baseline, candidate)

    assert "already-tagged dialogue exactly once" in candidate["shots"][0]["action"]
    assert "already-tagged remote dialogue exactly once" in candidate["shots"][0]["sound"]
    assert not any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_phone_normalisation_does_not_hide_unscripted_words():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [subject_id]
    shot["dialogue"] = [{
        "id": "e8681c94-8fc4-47c5-a40d-e05ec917ef26", "speaker_id": subject_id,
        "text": "Stay.", "language": "English", "delivery": "quiet",
        "locked": True, "voiceover": False,
    }]
    shot["action"] = "Koko says an unscripted goodbye."

    candidate = normalise_structured_dialogue_directions(baseline)
    report = review_generated_plan(production, segment, baseline, candidate)

    assert candidate["shots"][0]["action"] == "Koko says an unscripted goodbye."
    assert any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_local_phone_line_turns_an_unauthorised_remote_reply_into_silence():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Besi", "description": "", "asset_ids": []}]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [subject_id]
    shot["dialogue"] = [{
        "id": "e8681c94-8fc4-47c5-a40d-e05ec917ef26", "speaker_id": subject_id,
        "text": "Are you there?", "language": "English", "delivery": "quiet",
        "locked": True, "voiceover": False,
    }]
    shot["action"] = "Besi speaks into the phone, then pauses while listening to the response."
    shot["sound"] = "The muffled sound of a voice coming from the smartphone speaker."

    candidate = normalise_structured_dialogue_directions(baseline)
    report = review_generated_plan(production, segment, baseline, candidate)

    assert "no reply occurs in this clip" in candidate["shots"][0]["action"]
    assert "non-vocal smartphone-speaker noise" in candidate["shots"][0]["sound"]
    assert not any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_last_resort_speech_repair_preserves_structured_words_and_speaker():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Besi", "description": "", "asset_ids": []}]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [subject_id]
    shot["dialogue"] = [{
        "id": "e8681c94-8fc4-47c5-a40d-e05ec917ef26", "speaker_id": subject_id,
        "text": "This exact line stays.", "language": "English", "delivery": "quiet",
        "locked": True, "voiceover": False,
    }]
    shot["action"] = "Besi suddenly improvises and says several additional words."
    original_dialogue = copy.deepcopy(shot["dialogue"])

    repaired = silence_unstructured_speech_directions(baseline)
    report = review_generated_plan(production, segment, baseline, repaired)

    assert repaired["shots"][0]["dialogue"] == original_dialogue
    assert "already-tagged dialogue cue exactly once" in repaired["shots"][0]["action"]
    assert not any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_last_resort_vocal_repair_preserves_dialogue_and_silences_ambiguous_laugh():
    production, segment, baseline = fixture()
    segment["duration"] = 5
    koko_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    besi_id = "f2ae0b7c-06ed-460d-aac4-bc8b59e97995"
    baseline["subjects"] = [
        {"id": koko_id, "name": "Koko", "description": "", "asset_ids": []},
        {"id": besi_id, "name": "Besi", "description": "", "asset_ids": []},
    ]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [koko_id, besi_id]
    shot["dialogue"] = [
        {"id": "line-1", "speaker_id": koko_id, "text": "Stay.",
         "language": "English", "delivery": "quiet", "locked": True, "voiceover": False},
        {"id": "line-2", "speaker_id": besi_id, "text": "I will.",
         "language": "English", "delivery": "quiet", "locked": True, "voiceover": False},
    ]
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["action"] = (
        "They share a moment of comfortable silence before Koko speaks, followed by Besi's response. "
        "They exchange a look. They relax their shoulders. They turn toward the window."
    )
    candidate["shots"][0]["sound"] = "A wordless laugh follows."
    original_dialogue = copy.deepcopy(candidate["shots"][0]["dialogue"])

    repaired = silence_unstructured_speech_directions(candidate, baseline)
    report = review_generated_plan(production, segment, baseline, repaired)

    assert repaired["shots"][0]["dialogue"] == original_dialogue
    assert report["status"] == "passed"
    assert not {"unstructured_speech_event", "unanchored_nonverbal_vocal"} & {
        row["code"] for row in report["issues"]
    }


def test_last_resort_vocal_repair_keeps_an_authored_named_wordless_event():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [
        {"id": subject_id, "name": "Koko", "description": "", "asset_ids": []},
    ]
    baseline["shots"][0]["action"] = "Koko releases one brief sigh and lowers the note."

    repaired = silence_unstructured_speech_directions(
        copy.deepcopy(baseline), baseline)

    assert "Koko releases one brief sigh" in repaired["shots"][0]["action"]
    assert review_generated_plan(production, segment, baseline, repaired)["status"] == "passed"


def test_locked_final_state_and_soundscape_are_audio_normalised_before_planning():
    production, segment, baseline = fixture()
    segment["duration"] = 5
    koko_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    besi_id = "f2ae0b7c-06ed-460d-aac4-bc8b59e97995"
    baseline["subjects"] = [
        {"id": koko_id, "name": "Koko", "description": "", "asset_ids": []},
        {"id": besi_id, "name": "Besi", "description": "", "asset_ids": []},
    ]
    shot = baseline["shots"][0]
    shot["visible_subject_ids"] = [koko_id]
    shot["offscreen_subject_ids"] = [besi_id]
    shot["final_state"] = (
        "Both remain in their separate video-call screens, sharing a soft laugh.")
    baseline["soundscape"] = (
        "Ambient silence of two separate rooms, followed by the intimate quality "
        "of the video call voices.")

    safe_baseline = silence_unstructured_speech_directions(
        copy.deepcopy(baseline), baseline)
    candidate = copy.deepcopy(safe_baseline)
    candidate["shots"][0]["action"] = (
        "They share a brief silence before Koko speaks, followed by Besi's response. "
        "They glance aside. They pause.")
    safe_candidate = silence_unstructured_speech_directions(
        candidate, safe_baseline)
    report = review_generated_plan(
        production, segment, safe_baseline, safe_candidate)

    assert "separate video-call screens" in safe_baseline["shots"][0]["final_state"]
    assert "silent visual amusement" in safe_baseline["shots"][0]["final_state"]
    assert "laugh" not in safe_baseline["shots"][0]["final_state"]
    assert "voices" not in safe_baseline["soundscape"]
    assert report["status"] == "passed"
    assert not {"unstructured_speech_event", "unanchored_nonverbal_vocal"} & {
        row["code"] for row in report["issues"]
    }


def test_generated_plan_does_not_treat_voice_metadata_as_an_audible_event():
    production, segment, baseline = fixture()
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["performance"] = "Her established voice identity is warm and restrained."

    assert review_generated_plan(production, segment, baseline, candidate)["status"] == "passed"


def test_generated_plan_quality_rejects_internal_cut_and_locked_changes():
    production, segment, baseline = fixture()
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["action"] = "Wide shot. Cut to a close-up."
    candidate["shots"][0]["setting"] = "A different location."
    candidate["style"]["genre"] = "photoreal"

    report = review_generated_plan(production, segment, baseline, candidate)

    assert report["status"] == "failed"
    codes = {row["code"] for row in report["issues"]}
    assert "locked_style_changed" in codes
    assert "locked_setting_changed" in codes
    assert "internal_cut" in codes
    assert "multiple_camera_views" in codes
    request = repair_request(report)
    assert "preserve the exact source story, dialogue" in request
    assert "Replace the cut with a continuous pan" in request


def test_angle_plus_framing_describes_one_camera_view():
    production, segment, baseline = fixture()
    baseline["shots"][0]["setting"] = (
        "A low-angle close-up on the edge of the glass paperweight.")

    report = review_generated_plan(
        production, segment, baseline, copy.deepcopy(baseline))

    assert report["status"] == "passed"
    assert not any(row["code"] == "multiple_camera_views"
                   for row in report["issues"])


def test_generated_plan_keeps_dense_action_as_an_advisory_warning():
    production, segment, baseline = fixture()
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["action"] = (
        "Koko opens the drawer. She finds the card. She lifts it. She reads it. "
        "She turns it over. She reaches for a pen. She writes a reply. She pauses. "
        "She folds the card. She places it beside the phone."
    )

    report = review_generated_plan(production, segment, baseline, candidate)

    assert report["status"] == "passed"
    issue = next(row for row in report["issues"] if row["code"] == "dense_generated_action")
    assert issue["severity"] == "warning"


def test_generated_plan_does_not_count_machine_guard_sentences_as_action_beats():
    production, segment, baseline = fixture()
    segment["duration"] = 5
    baseline["shots"][0]["action"] = (
        "Koko writes the note and pauses.\n\n"
        "TEMPORAL CAST AND STATE LOCK — CURRENT CLIP\n"
        "Visible at opening: Koko. Enter physically: none. Exit physically: none. "
        "Visible in final frame: Koko. Off-screen: none. Mentioned only: none.")

    report = review_generated_plan(
        production, segment, baseline, copy.deepcopy(baseline))

    assert not any(row["code"] == "dense_generated_action" for row in report["issues"])


def test_directed_plan_cannot_replace_the_locked_visual_style():
    _production, _segment, baseline = fixture()
    baseline.setdefault("simple", {})["directed"] = True
    proposal = {
        "shots": [copy.deepcopy(baseline["shots"][0])],
        "style": {"genre": "photoreal horror", "lighting": "neon red"},
    }

    candidate = merge_plan(baseline, proposal)

    assert candidate["style"] == baseline["style"]


def test_generated_plan_quality_rejects_dialogue_or_cast_plane_reassignment():
    production, segment, baseline = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    baseline["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    source = baseline["shots"][0]
    source["visible_subject_ids"] = [subject_id]
    source["dialogue"] = [{"id": "b2f71fd8-ea5f-48a7-abdb-edc32f382cc6",
                            "speaker_id": subject_id, "text": "Stay.", "language": "English",
                            "delivery": "quiet", "locked": True, "voiceover": False}]
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["dialogue"][0]["text"] = "Go."
    candidate["shots"][0]["display_subject_ids"] = [subject_id]

    report = review_generated_plan(production, segment, baseline, candidate)

    codes = {row["code"] for row in report["issues"]}
    assert "dialogue_changed" in codes
    assert "locked_display_subject_ids_changed" in codes
    assert "cast_plane_overlap" in codes


def test_compiled_prompt_gate_requires_exact_cues_language_and_final_audio_lock():
    production, segment, project = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    project["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    project["shots"][0]["dialogue"] = [{
        "id": "b2f71fd8-ea5f-48a7-abdb-edc32f382cc6", "speaker_id": subject_id,
        "text": "Stay.", "language": "English", "delivery": "quiet",
        "locked": True, "voiceover": False,
    }]
    prompt = (
        "dialogue_and_audio:\nKoko: <d>[English] Stay.</d>\n\n"
        "FINAL AUDIO OVERRIDE — HIGHEST PRIORITY: Spoken-utterance count is exactly "
        "1 utterance total (cue 1=Koko)."
    )
    passed = review_compiled_prompt(production, segment, project, prompt)
    assert passed["status"] == "passed"
    assert len(passed["prompt_sha256"]) == 64

    failed = review_compiled_prompt(
        production, segment, project,
        prompt.replace("[English]", "[Chinese]").replace("FINAL AUDIO OVERRIDE", "AUDIO RULE"),
    )
    assert failed["status"] == "failed"
    codes = {row["code"] for row in failed["issues"]}
    assert {"dialogue_language_tag", "final_audio_lock"} <= codes
    assert all(not row["repairable"] for row in failed["issues"])


def test_compiled_prompt_gate_checks_sound_fields_even_without_ai_planning():
    production, segment, project = fixture()
    subject_id = "8687ec74-29b9-439d-bd2d-c65847566029"
    project["subjects"] = [{"id": subject_id, "name": "Koko", "description": "", "asset_ids": []}]
    project["shots"][0]["sound"] = "A soft voice whispers from the phone."
    prompt = "FINAL AUDIO OVERRIDE — HIGHEST PRIORITY: Spoken-utterance count is exactly zero."

    report = review_compiled_prompt(production, segment, project, prompt)

    assert report["status"] == "failed"
    assert any(row["code"] == "unstructured_speech_event" for row in report["issues"])


def test_prompt_quality_history_and_storage_are_bounded():
    production, segment, baseline = fixture()
    candidate = copy.deepcopy(baseline)
    candidate["shots"][0]["action"] = "Cut to another angle."
    failure = review_generated_plan(production, segment, baseline, candidate)
    final = review_compiled_prompt(
        production, segment, baseline,
        "FINAL AUDIO OVERRIDE — HIGHEST PRIORITY: Spoken-utterance count is exactly zero.",
    )
    saved = normalise_prompt_quality(with_repair_history(final, [failure]))
    assert saved["status"] == "passed"
    assert saved["attempts"] == 1
    assert saved["repairs"]
