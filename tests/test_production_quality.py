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


def test_boundary_absence_is_hard_only_for_the_character_required_in_frame():
    wrong_person = normalise_visual_review(_visual_review({
        "code": "missing_required_character", "severity": "error", "confidence": 1,
        "frame": "first", "message": "Nox is missing from the first frame.",
        "repair_instruction": "Add Nox to the first frame.",
    }), has_identity_references=True, expected_first=["Mimi"], expected_last=["Mimi"])
    required_person = normalise_visual_review(_visual_review({
        "code": "missing_required_character", "severity": "error", "confidence": 1,
        "frame": "first", "message": "Mimi is missing from the first frame.",
        "repair_instruction": "Add Mimi to the first frame.",
    }), has_identity_references=True, expected_first=["Mimi"], expected_last=["Mimi"])

    assert wrong_person["status"] == "warning"
    assert wrong_person["issues"][0]["confidence"] == .69
    assert required_person["status"] == "failed"


def test_passive_continuity_prop_cannot_be_misclassified_as_identity_failure():
    result = normalise_visual_review(_visual_review({
        "code": "identity_drift", "severity": "error", "confidence": 1,
        "frame": "multiple",
        "message": "Bokka is missing the required Three-Button Remote in her right hand.",
        "repair_instruction": "Add the Three-Button Remote to Bokka's right hand.",
    }), has_identity_references=True, expected_first=["Bokka"], expected_last=["Bokka"],
        continuity_prop_names=["Three-Button Remote"], strict_prop_or_device=True)

    assert result["status"] == "warning"
    assert result["issues"][0]["code"] == "prop_or_device_error"
    assert result["issues"][0]["confidence"] == .79
    assert not repair_direction(result["issues"])


def test_noncritical_prop_or_device_issue_is_warning_but_real_duplicate_stays_hard():
    prop = normalise_visual_review(_visual_review({
        "code": "prop_or_device_error", "severity": "error", "confidence": .97,
        "frame": "first", "message": "A passive prop is not visible.",
        "repair_instruction": "Show the prop.",
    }), has_identity_references=True, expected_first=[], expected_last=[],
        strict_prop_or_device=False)
    duplicate = normalise_visual_review(_visual_review({
        "code": "duplicate_character", "severity": "error", "confidence": .97,
        "frame": "last", "message": "Mimi appears as two independent bodies.",
        "repair_instruction": "Keep one physical Mimi.",
    }), has_identity_references=True, expected_first=[], expected_last=[],
        strict_prop_or_device=False)

    assert prop["status"] == "warning"
    assert duplicate["status"] == "failed"


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
