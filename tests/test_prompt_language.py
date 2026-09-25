import copy

import pytest

from backend.compiler import compile_project
from backend.projects import new_project, shot, uid
from backend.prompt_language import (
    japanese_hiragana,
    localise_h3_prompt,
    prompt_sha256,
    validate_delivery_prompt,
)


class FakeClient:
    def __init__(self, reply):
        self.replies = copy.deepcopy(reply if isinstance(reply, list) else [reply])
        self.request = None
        self.requests = []

    def complete_json(self, model, system, content, schema, **kwargs):
        self.request = {"model": model, "system": system, "content": content,
                        "schema": schema, "kwargs": kwargs}
        self.requests.append(copy.deepcopy(self.request))
        return copy.deepcopy(self.replies.pop(0))


class RejectingClient:
    def complete_json(self, *args, **kwargs):
        raise AssertionError("An already compliant English prompt must not call the model twice.")


def sample_project(language="zh-CN"):
    project = new_project()
    project.update(mode="t2va", duration=5, production_language=language)
    person = uid()
    project["subjects"] = [{"id": person, "name": "美咲", "description": "黒い髪の女性", "asset_ids": []}]
    scene = shot(5)
    scene.update({
        "setting": "安静的图书馆", "action": "女孩走进图书馆。", "final_state": "她停在书架前。",
        "visible_subject_ids": [person],
        "dialogue": [{"id": uid(), "speaker_id": person, "text": "Good morning.",
                      "language": "English", "delivery": "平静地", "locked": True, "voiceover": False}],
    })
    project["shots"] = [scene]
    return project


def test_localisation_separates_english_direction_from_target_dialogue():
    raw = "integrated_multimodal_description: [Shot 1] 安静的图书馆. <Subject 1> 美咲 says: <d>[English] Good morning.</d>\n\noverall_soundscape: 安静.\n\nnon_diegetic_music: N/A"
    client = FakeClient({
        "direction_segments": [
            {"index": 0, "text": "A quiet library."},
            {"index": 1, "text": "Misaki says:"},
            {"index": 2, "text": "Quiet room tone."},
        ],
        "dialogue": [{"index": 0, "text": "早上好。"}],
    })
    record = localise_h3_prompt(client, "local-model", raw, "zh-CN")
    assert record["source_sha256"] == prompt_sha256(raw)
    assert "A quiet library" in record["prompt"]
    assert "<d>[Chinese] 早上好。</d>" in record["prompt"]
    assert "安静的图书馆" not in record["prompt"]
    assert client.request["kwargs"]["max_tokens"] == 4096
    assert "direction_segments" in client.request["content"]
    assert "[Shot 1]" not in client.request["content"]
    assert "<Subject 1>" not in client.request["content"]
    assert "integrated_multimodal_description:" not in client.request["content"]
    assert "N/A" not in client.request["content"]


def test_compliant_english_delivery_uses_validated_zero_inference_fast_path():
    raw = (
        "integrated_multimodal_description: [Shot 1] A quiet room. "
        "<Subject 1> Pokke says: <d>[English] Breakfast first.</d>\n\n"
        "overall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A"
    )
    record = localise_h3_prompt(RejectingClient(), "gemma4:31b", raw, "en")
    assert record["prompt"] == raw
    assert record["source_sha256"] == prompt_sha256(raw)


def test_narrative_storyboard_preserves_section_and_beat_syntax():
    raw = (
        "asset_roles:\n<Picture 1>: Pokke identity.\n\n"
        "visual_style_and_continuity:\n[Beat 1] A quiet room.\n\n"
        "dialogue_and_audio:\nPokke (S1): <d>[English] Wait.</d>\n\n"
        "overall_soundscape:\nRoom tone.\n\n"
        "non_diegetic_music:\nN/A\n\n"
        "stability_constraints:\nOnly one Pokke."
    )
    record = localise_h3_prompt(RejectingClient(), "local-model", raw, "en")
    assert record["prompt"] == raw
    assert record["source_sha256"] == prompt_sha256(raw)


def test_compiler_uses_only_a_current_source_bound_language_pass():
    project = sample_project()
    raw = compile_project(project)
    assert raw["valid"]
    translated = raw["prompt"].replace("安静的图书馆", "a quiet library").replace("女孩走进图书馆", "the woman enters the library").replace("她停在书架前", "she stops by the shelf").replace("黒い髪の女性", "a woman with black hair").replace("平静地", "calmly").replace("美咲", "Misaki")
    translated = translated.replace("<d>[English] Good morning.</d>", "<d>[Chinese] 早上好。</d>")
    project["h3_prompt_translation"] = {"version": 1, "source_sha256": prompt_sha256(raw["prompt"]),
                                        "target_language": "zh-CN", "prompt": translated}
    compiled = compile_project(project)
    assert compiled["valid"]
    assert compiled["prompt"] == translated
    project["shots"][0]["action"] += " 她挥手。"
    stale = compile_project(project)
    assert not stale["valid"]
    assert any(issue["code"] == "stale_prompt_translation" for issue in stale["issues"])


def test_japanese_delivery_is_hiragana_only():
    assert japanese_hiragana("今日は図書館へ行きます。カメラ") == "きょうはとしょかんへいきます。かめら"
    validate_delivery_prompt(
        "integrated_multimodal_description: [Shot 1] A woman enters. <d>[Japanese] きょうはとしょかんへいきます。</d>",
        "ja",
    )
    with pytest.raises(ValueError, match="hiragana"):
        validate_delivery_prompt(
            "integrated_multimodal_description: [Shot 1] A woman enters. <d>[Japanese] 今日は図書館へ行きます。</d>",
            "ja",
        )


def test_language_pass_rejects_non_english_direction_or_changed_tokens():
    raw = "integrated_multimodal_description: [Shot 1] 场景. <Picture 1> <d>[Chinese] 你好。</d>"
    bad_language = FakeClient([
        {"direction_segments": [{"index": 0, "text": "场景."}],
         "dialogue": [{"index": 0, "text": "你好。"}]},
        {"direction_segments": [{"index": 0, "text": "仍是场景."}], "dialogue": []},
    ])
    with pytest.raises(ValueError, match="non-English"):
        localise_h3_prompt(bad_language, "model", raw, "zh-CN")
    injected_reference = FakeClient({
        "direction_segments": [{"index": 0, "text": "A scene with <Picture 2>."}],
        "dialogue": [{"index": 0, "text": "你好。"}],
    })
    with pytest.raises(ValueError, match="inserted protected H3"):
        localise_h3_prompt(injected_reference, "model", raw, "zh-CN")


def test_language_pass_retries_only_direction_segments_with_cjk():
    raw = "integrated_multimodal_description: [Shot 1] 安静的图书馆. <d>[Chinese] 你好。</d>"
    client = FakeClient([
        {"direction_segments": [{"index": 0, "text": "安静的图书馆."}],
         "dialogue": [{"index": 0, "text": "こんにちは。"}]},
        {"direction_segments": [{"index": 0, "text": "A quiet library."}], "dialogue": []},
    ])
    record = localise_h3_prompt(client, "model", raw, "ja")
    assert len(client.requests) == 2
    assert "A quiet library." in record["prompt"]
    assert "<d>[Japanese] こんにちは。</d>" in record["prompt"]


def test_english_voice_bible_is_preserved_verbatim_and_hidden_from_model():
    voice_block = (
        "VOICE DIRECTION — CURRENT CLIP ONLY\n\n"
        "Series Voice Style:\n"
        "Neutral American English; clean close studio dialogue; never robotic.\n\n"
        "Voice Card — Pokke:\n"
        "A fast-paced, highly expressive and bouncy animated boy companion voice."
    )
    raw = (
        "integrated_multimodal_description: [Shot 1] 安静的图书馆. "
        + voice_block
        + " <d>[English] Good morning.</d>"
    )
    client = FakeClient({
        "direction_segments": [{"index": 0, "text": "A quiet library."}],
        "dialogue": [{"index": 0, "text": "Good morning."}],
    })
    record = localise_h3_prompt(
        client, "model", raw, "en", verbatim_blocks=[voice_block],
    )
    assert voice_block in record["prompt"]
    assert voice_block not in client.request["content"]
    assert "H3VERBATIM" not in record["prompt"]


def test_localisation_preserves_english_overview_binding_when_only_timing_is_cjk():
    binding = (
        "<Subject 1> is Yuki. <Picture 1> supplies character identity "
        "(use the labelled region for Yuki; assigned to Yuki; assigned image pixels lead visible appearance)."
    )
    raw = (
        "subject_definitions:\n" + binding + "\n\n"
        "detailed_description:\n[Shot 1] AUTHORED INTERNAL TIMING: 0.00-5.00s: 镜头慢慢拉远。"
    )
    client = FakeClient({
        "direction_segments": [{"index": 0, "text": "The camera slowly pulls back."}],
        "dialogue": [],
    })

    record = localise_h3_prompt(client, "gemma4:31b", raw, "en")

    assert binding in record["prompt"]
    assert "The camera slowly pulls back." in record["prompt"]
    assert binding not in client.request["content"]
