import copy
import time

from backend.production_automation import (
    ProductionAutomationManager,
    is_prompt_repairable,
    is_transient_failure,
)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("background production did not reach the expected state")


def fake_manager(*, submit_failures=None, initial_status="idle", quality_result=None,
                 rejected_take=False):
    state = {
        "id": "production-1", "task_state": "active",
        "segments": [{"id": "segment-1", "index": 1,
                      "status": "ready" if rejected_take else "stale",
                      "stale_reasons": [] if rejected_take else ["old prompt"],
                      "video_prompt": "current" if rejected_take else ""}],
        "automation": {"status": initial_status, "stage": "scan", "merge": True,
                       "completed": 0, "total": 1, "current_index": 0,
                       "attempt": 0, "last_error": ""},
    }
    rendered = set()
    submitted_ids = []
    merged = []
    failures = list(submit_failures or [])

    def get_production(_ident):
        return copy.deepcopy(state)

    def update_automation(_ident, changes):
        state["automation"].update(copy.deepcopy(changes))
        return copy.deepcopy(state)

    def outputs(_ident):
        if rejected_take:
            rejected = {"id": "run-old", "status": "succeeded",
                        "quality_review": {"status": "failed", "accepted": False}}
            return {"segments": [{"segment_id": "segment-1", "index": 1,
                                  "title": "Rejected clip", "selected": None,
                                  "candidates": [rejected]}], "all_ready": False,
                    "quality_pending_count": 0}
        selected = {"id": "run-1", "status": "succeeded"} if "segment-1" in rendered else None
        return {"segments": [{"segment_id": "segment-1", "selected": selected,
                              "candidates": []}], "all_ready": bool(selected)}

    def generate_prompt(_production_id, _segment_id):
        state["segments"][0].update(status="ready", stale_reasons=[], video_prompt="current")

    def submit_video(_production_id, _segment_id, request_id):
        submitted_ids.append(request_id)
        if failures:
            raise RuntimeError(failures.pop(0))
        return {"run": {"id": "run-1", "status": "queued", "stage": "Queued"}}

    def get_run(_run_id):
        rendered.add("segment-1")
        return {"id": "run-1", "status": "succeeded", "stage": "Video ready"}

    manager = ProductionAutomationManager(
        list_productions=lambda: [{"id": state["id"]}],
        get_production=get_production, update_automation=update_automation,
        outputs=outputs, generate_prompt=generate_prompt, submit_video=submit_video,
        get_run=get_run, resolve_run=lambda _run_id: get_run(_run_id),
        build_film=lambda ident: merged.append(ident), poll_interval=0,
        review_quality=(lambda _ident: copy.deepcopy(quality_result))
        if quality_result is not None else None,
        retry_delays=(0, 0, 0),
    )
    return manager, state, submitted_ids, merged


def test_background_production_persists_cursor_and_completes_missing_work():
    manager, state, submitted_ids, merged = fake_manager()

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "completed")

    assert len(submitted_ids) == 1
    assert merged == [state["id"]]
    assert state["automation"]["completed"] == 1
    assert state["automation"]["run_id"] is None
    assert state["automation"]["request_id"] is None


def test_transient_submission_reuses_idempotency_key_then_continues():
    manager, state, submitted_ids, merged = fake_manager(
        submit_failures=["Connection timed out while contacting ComfyUI"])

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "completed")

    assert len(submitted_ids) == 2
    assert submitted_ids[0] == submitted_ids[1]
    assert merged == [state["id"]]


def test_semantic_failure_stops_for_attention_without_destructive_loop():
    manager, state, submitted_ids, merged = fake_manager(
        submit_failures=["Visible character identity references are missing: Mimi"])

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "needs_attention")

    assert len(submitted_ids) == 1
    assert not merged
    assert "identity references" in state["automation"]["last_error"]


def test_persisted_running_cursor_is_recovered_after_process_restart():
    manager, state, submitted_ids, merged = fake_manager(initial_status="running")

    manager.recover()
    wait_for(lambda: state["automation"]["status"] == "completed")

    assert len(submitted_ids) == 1
    assert merged == [state["id"]]


def test_failure_classification_is_narrow_and_safe():
    assert is_prompt_repairable("This production clip is stale. Regenerate it.")
    assert is_prompt_repairable(
        "shot_contract.relation_previous disagrees with transition_mode.")
    assert is_transient_failure("ReadTimeout contacting ComfyUI")
    assert is_transient_failure("A video request is already active.")
    assert not is_transient_failure("Visible character identity references are missing")


def test_failed_quality_review_stops_without_rerendering_or_merging():
    manager, state, submitted_ids, merged = fake_manager(quality_result={
        "failed": [{"segment_id": "segment-1", "status": "failed"}],
        "reviews": [], "pending": 0,
    })

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "needs_attention")

    assert len(submitted_ids) == 1
    assert not merged
    assert state["automation"]["stage"] == "quality"
    assert "approve" in state["automation"]["last_error"].lower()


def test_passing_quality_review_allows_final_merge():
    manager, state, submitted_ids, merged = fake_manager(quality_result={
        "failed": [], "reviews": [{"status": "passed"}], "pending": 0,
    })

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "completed")

    assert len(submitted_ids) == 1
    assert merged == [state["id"]]


def test_resuming_does_not_rerender_a_rejected_take_without_human_approval():
    manager, state, submitted_ids, merged = fake_manager(
        rejected_take=True,
        quality_result={"failed": [], "reviews": [], "pending": 0},
    )

    manager.start(state["id"])
    wait_for(lambda: state["automation"]["status"] == "needs_attention")

    assert submitted_ids == []
    assert merged == []
    assert "not adopted or rerendered automatically" in state["automation"]["last_error"]
    assert "1 Rejected clip" in state["automation"]["last_error"]
