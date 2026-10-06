import base64
import io

from PIL import Image

from backend.production_quality import (
    contact_sheet,
    media_issues,
    normalise_visual_review,
    repair_direction,
)


def _visual_review(issue):
    return {
        "summary": "Sampled-frame review.",
        "observed_cast_counts": {"first": 1, "middle": 1, "last": 1},
        "issues": [issue],
    }


def test_media_preflight_rejects_missing_video_and_dialogue_audio():
    issues = media_issues({"streams": [], "format": {"duration": "10"}}, 10, True)
    assert {row["code"] for row in issues} == {
        "missing_video_stream", "missing_dialogue_audio"
    }
    assert all(row["severity"] == "error" for row in issues)


def test_media_preflight_warns_without_rejecting_duration_drift():
    probe = {
        "streams": [{"codec_type": "video", "disposition": {"attached_pic": 0}}],
        "format": {"duration": "15"},
    }
    issues = media_issues(probe, 10, False)
    assert [(row["code"], row["severity"]) for row in issues] == [
        ("duration_mismatch", "warning")
    ]


def test_clear_duplicate_character_is_rejected_and_has_bounded_repair():
    result = normalise_visual_review(_visual_review({
        "code": "duplicate_character", "severity": "error", "confidence": .96,
        "frame": "middle", "message": "One identity appears as two bodies.",
        "repair_instruction": "Show exactly one physical instance of Koko.",
    }), has_identity_references=True, expected_first=1, expected_last=1)

    assert result["status"] == "failed"
    direction = repair_direction(result["issues"])
    assert "exactly one physical instance" in direction
    assert "preserve the exact story" in direction


def test_sampled_absence_and_unreferenced_identity_are_only_warnings():
    absent = normalise_visual_review(_visual_review({
        "code": "missing_required_character", "severity": "error", "confidence": .98,
        "frame": "middle", "message": "A person is absent in the middle sample.",
        "repair_instruction": "Keep the character visible.",
    }), has_identity_references=True, expected_first=1, expected_last=1)
    identity = normalise_visual_review(_visual_review({
        "code": "identity_drift", "severity": "error", "confidence": .99,
        "frame": "first", "message": "The face may differ.",
        "repair_instruction": "Preserve the face.",
    }), has_identity_references=False, expected_first=1, expected_last=1)

    assert absent["status"] == "warning"
    assert identity["status"] == "warning"
    assert not repair_direction(absent["issues"])
    assert not repair_direction(identity["issues"])


def test_contact_sheet_contains_generated_samples_and_references(tmp_path):
    frames = {}
    for index, kind in enumerate(("first", "middle", "last")):
        path = tmp_path / f"{kind}.png"
        Image.new("RGB", (64, 48), (index * 40, 80, 120)).save(path)
        frames[kind] = path
    reference = tmp_path / "reference.png"
    Image.new("RGB", (40, 80), (200, 140, 80)).save(reference)

    result = contact_sheet(frames, [{"path": reference, "label": "Koko"}])
    payload = base64.b64decode(result.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "JPEG"
        assert image.width > image.height
