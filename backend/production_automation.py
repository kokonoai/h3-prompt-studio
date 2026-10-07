"""Durable orchestration for production prompts, renders and final assembly.

The browser is only a controller.  Every cursor needed to resume after a page
reload or server restart is persisted on the production record before work is
submitted.  Video request IDs are also stable across transient retries so a
lost HTTP response cannot create a duplicate ComfyUI job.
"""
from __future__ import annotations

import threading
import time
import uuid


ACTIVE_STATUSES = frozenset({"running", "retrying"})
RUNNING_VIDEO_STATUSES = frozenset({"preparing", "queued", "running"})
PROMPT_REPAIR_MARKERS = (
    "directions inherited from an older clip",
    "outdated character-reference assignment",
    "production clip is stale",
    # The saved delivery-language prompt is derived from the English scene
    # prompt. Scene or language edits invalidate that derivative, but this is
    # normal rebuildable work, not a reason to stop the full-episode queue.
    "scene changed after its english h3 prompt was created",
    "saved h3 language pass is invalid",
    "project dialogue language changed",
    "shot_contract.relation_previous disagrees with transition_mode",
    # Adjacent-state failures can be repaired without changing story/cast: the
    # materialiser converts the unsafe continuation to a hard cut, rebuilds the
    # prompt, and this worker continues from the same clip.
    "continuous clip adds characters at its opening",
    "continuous clip loses characters at its opening",
    "remote/off-screen identities entered the real space without a cut",
    "changes position/facing across a continuous cut",
    "reverses movement direction across a continuous cut",
    "eyeline direction flips across a continuous cut",
    "changes holder from",
    "changes visible state across a continuous boundary",
    "wardrobe-card selection changes across a continuous boundary",
    "camera axis changes during continuous action",
    "previous-ending continuation is enabled but this transition is not mmh3-safe",
)
TRANSIENT_MARKERS = (
    "timeout", "timed out", "readtimeout", "connecttimeout", "connection",
    "not reachable", "temporarily unavailable", "winerror 10054",
    "winerror 5", "permissionerror", "ai is still working",
    "a video request is already active",
    "running or queued work", "already generating another video",
    "has not released gpu memory", "model has not released",
)


def is_prompt_repairable(message: str) -> bool:
    value = str(message).casefold()
    return any(marker in value for marker in PROMPT_REPAIR_MARKERS)


def is_transient_failure(message: str) -> bool:
    value = str(message).casefold()
    return any(marker in value for marker in TRANSIENT_MARKERS)


class ProductionAutomationManager:
    """One persistent, idempotent worker per production."""

    def __init__(self, *, list_productions, get_production, update_automation,
                 outputs, generate_prompt, submit_video, get_run, resolve_run,
                 build_film, review_quality=None, poll_interval=5.0,
                 retry_delays=(5.0, 15.0, 30.0)):
        self.list_productions = list_productions
        self.get_production = get_production
        self.update_automation = update_automation
        self.outputs = outputs
        self.generate_prompt = generate_prompt
        self.submit_video = submit_video
        self.get_run = get_run
        self.resolve_run = resolve_run
        self.review_quality = review_quality
        self.build_film = build_film
        self.poll_interval = poll_interval
        self.retry_delays = tuple(retry_delays)
        self.lock = threading.RLock()
        self.workers: dict[str, threading.Thread] = {}
        self.stopping = threading.Event()

    def _update(self, production_id, **changes):
        changes["updated_at"] = time.time()
        return self.update_automation(production_id, changes)

    def _worker_alive(self, production_id):
        worker = self.workers.get(production_id)
        return bool(worker and worker.is_alive())

    def start(self, production_id, *, merge=True, requested_by="user"):
        production = self.get_production(production_id)
        if production.get("task_state") == "paused":
            raise ValueError("This production is paused. Resume it before starting the background run.")
        if not production.get("segments"):
            raise ValueError("Create the current episode storyboard before starting the background run.")
        with self.lock:
            if self._worker_alive(production_id):
                return self.get_production(production_id).get("automation", {})
            current = production.get("automation", {})
            started_at = current.get("started_at") or time.time()
            self._update(
                production_id, status="running", stage="scan",
                merge=bool(merge), requested_by=requested_by, started_at=started_at,
                finished_at=None, last_error="", stage_detail="Checking missing work",
            )
            worker = threading.Thread(
                target=self._run, args=(production_id,), daemon=True,
                name="h3-production-" + production_id[:8],
            )
            self.workers[production_id] = worker
            worker.start()
        return self.get_production(production_id).get("automation", {})

    def ensure(self, production_id):
        """Restart a persisted active job whose in-memory worker disappeared."""
        production = self.get_production(production_id)
        automation = production.get("automation", {})
        if (automation.get("status") in ACTIVE_STATUSES
                and production.get("task_state") == "active"):
            with self.lock:
                if not self._worker_alive(production_id):
                    worker = threading.Thread(
                        target=self._run, args=(production_id,), daemon=True,
                        name="h3-production-" + production_id[:8],
                    )
                    self.workers[production_id] = worker
                    worker.start()
        return automation

    def recover(self):
        for summary in self.list_productions():
            try:
                self.ensure(summary["id"])
            except (OSError, ValueError, KeyError):
                continue

    def shutdown(self):
        self.stopping.set()

    @staticmethod
    def _selected_segment_ids(overview):
        return {
            row.get("segment_id") for row in overview.get("segments", [])
            if isinstance(row.get("selected"), dict)
            and row["selected"].get("status") == "succeeded"
        }

    @staticmethod
    def _quality_blocked_rows(overview, segments):
        """Return clips whose latest usable take awaits an explicit human decision.

        A rejected take is evidence, not missing work.  Treating it as an empty
        slot caused a resumed episode queue to rerender unrelated failed clips
        after the user approved only one repair.  Edited/stale clips and clips
        with an explicitly approved repair remain eligible for regeneration.
        """
        by_id = {row.get("id"): row for row in segments}
        blocked = []
        for row in overview.get("segments", []):
            segment = by_id.get(row.get("segment_id"), {})
            if row.get("selected") or segment.get("quality_repair_direction"):
                continue
            if segment.get("status") != "ready" or segment.get("stale_reasons"):
                continue
            rejected = [run for run in row.get("candidates", [])
                        if run.get("status") == "succeeded"
                        and run.get("quality_review", {}).get("status") == "failed"
                        and not run.get("quality_review", {}).get("accepted")]
            if rejected:
                blocked.append(row)
        return blocked

    @staticmethod
    def _quality_attention_message(rows):
        labels = []
        for row in rows[:6]:
            index = row.get("index")
            title = str(row.get("title") or "").strip()
            labels.append((f"{index} {title}" if index else
                           str(row.get("segment_id") or "")[:8]).strip())
        suffix = ": " + "; ".join(labels) if labels else "."
        return (
            f"Post-render quality review needs a human decision for {len(rows)} take(s). "
            "They were not adopted or rerendered automatically. Review each take and either "
            "accept it or approve its suggested repair" + suffix)

    @staticmethod
    def _active_run_for_segment(overview, segment_id):
        row = next((item for item in overview.get("segments", [])
                    if item.get("segment_id") == segment_id), None)
        if not row:
            return None
        return next((job for job in reversed(row.get("candidates", []))
                     if job.get("status") in RUNNING_VIDEO_STATUSES | {"uncertain"}), None)

    def _pause_if_requested(self, production_id):
        production = self.get_production(production_id)
        if production.get("task_state") != "paused":
            return False
        self._update(production_id, status="paused", stage="paused",
                     last_error="", run_id=None, request_id=None)
        return True

    def _wait(self, seconds):
        return self.stopping.wait(max(0.0, seconds))

    def _monitor_run(self, production_id, segment, run_id):
        while not self.stopping.is_set():
            job = self.get_run(run_id)
            status = job.get("status")
            self._update(
                production_id, status="running", stage="videos",
                current_segment_id=segment["id"], current_index=segment["index"],
                run_id=run_id, stage_detail=job.get("stage") or status or "video",
            )
            if status in RUNNING_VIDEO_STATUSES:
                self._wait(self.poll_interval)
                continue
            if status == "uncertain":
                job = self.resolve_run(run_id)
                if job.get("status") == "uncertain":
                    raise RuntimeError(job.get("error") or
                                       "The video submission is uncertain and needs manual review.")
                continue
            return job
        raise RuntimeError("Studio is shutting down; the persisted production task will resume next time.")

    def _process_segment(self, production_id, segment, overview):
        segment_id, index = segment["id"], segment["index"]
        active = self._active_run_for_segment(overview, segment_id)
        automation = self.get_production(production_id).get("automation", {})
        run_id = automation.get("run_id") or (active or {}).get("id")
        if run_id:
            job = self._monitor_run(production_id, segment, run_id)
            if job.get("status") == "succeeded":
                self._update(production_id, run_id=None, request_id=None, attempt=0,
                             last_error="", stage_detail="Video ready")
                return
            raise RuntimeError(job.get("error") or job.get("stage") or
                               f"Clip {index} video failed.")

        repaired = False
        request_id = automation.get("request_id") or str(uuid.uuid4())
        max_attempts = len(self.retry_delays) + 1
        for attempt in range(max_attempts):
            if self._pause_if_requested(production_id):
                return
            production = self.get_production(production_id)
            current = next((item for item in production.get("segments", [])
                            if item.get("id") == segment_id), None)
            if current is None:
                raise RuntimeError("The queued production clip no longer exists.")
            try:
                if current.get("status") != "ready" or not current.get("video_prompt", "").strip():
                    self._update(production_id, status="running", stage="prompts",
                                 current_segment_id=segment_id, current_index=index,
                                 stage_detail=f"Building prompt for clip {index}", attempt=attempt)
                    self.generate_prompt(production_id, segment_id)
                self._update(production_id, status="running", stage="videos",
                             current_segment_id=segment_id, current_index=index,
                             request_id=request_id, run_id=None,
                             stage_detail=f"Submitting clip {index}", attempt=attempt)
                submitted = self.submit_video(production_id, segment_id, request_id)
                run = submitted.get("run", submitted)
                run_id = run.get("id")
                if not run_id:
                    raise RuntimeError("Video submission did not return a durable run ID.")
                self._update(production_id, run_id=run_id, request_id=request_id,
                             stage_detail=run.get("stage") or "Video submitted")
                job = self._monitor_run(production_id, current, run_id)
                if job.get("status") == "succeeded":
                    self._update(production_id, run_id=None, request_id=None, attempt=0,
                                 last_error="", stage_detail="Video ready")
                    return
                # A returned terminal failure is confirmed not to be queued. A
                # later retry therefore receives a new idempotency key.
                request_id = str(uuid.uuid4())
                self._update(production_id, run_id=None, request_id=request_id)
                raise RuntimeError(job.get("error") or job.get("stage") or
                                   f"Clip {index} video failed.")
            except Exception as exc:
                message = str(exc)
                if is_prompt_repairable(message) and not repaired:
                    repaired = True
                    self._update(production_id, status="running", stage="prompts",
                                 stage_detail=f"Repairing prompt for clip {index}",
                                 last_error=message[:2000])
                    self.generate_prompt(production_id, segment_id)
                    continue
                if attempt + 1 < max_attempts and is_transient_failure(message):
                    delay = self.retry_delays[attempt]
                    self._update(production_id, status="retrying", stage="videos",
                                 attempt=attempt + 1, last_error=message[:2000],
                                 stage_detail=f"Retrying clip {index} in {int(delay)}s")
                    if self._wait(delay):
                        raise RuntimeError("Studio is shutting down; the task will resume next time.")
                    continue
                raise
        raise RuntimeError(f"Clip {index} exceeded its safe retry limit.")

    def _run(self, production_id):
        try:
            while not self.stopping.is_set():
                if self._pause_if_requested(production_id):
                    return
                production = self.get_production(production_id)
                segments = sorted(production.get("segments", []), key=lambda item: item["index"])
                overview = self.outputs(production_id)
                completed_ids = self._selected_segment_ids(overview)
                self._update(production_id, status="running", total=len(segments),
                             completed=len(completed_ids), last_error="")
                # An explicitly approved quality repair is the only rejected
                # clip the queue may regenerate.  Prioritise it so an earlier
                # unapproved rejection cannot hijack the resumed worker.
                target = next((segment for segment in segments
                               if segment.get("quality_repair_direction") and
                               (segment["id"] not in completed_ids
                                or segment.get("status") != "ready"
                                or not segment.get("video_prompt", "").strip()
                                or bool(segment.get("stale_reasons")))), None)
                if target is not None:
                    self._process_segment(production_id, target, overview)
                    continue

                # Newly rendered takes must receive their review before older
                # rejected clips pause the queue again.
                if (self.review_quality is not None and
                        int(overview.get("quality_pending_count") or 0) > 0):
                    self._update(production_id, status="running", stage="quality",
                                 current_segment_id=None, current_index=0,
                                 run_id=None, request_id=None,
                                 stage_detail="Reviewing completed takes")
                    # A failed take is quarantined by the quality reviewer.  It
                    # must never be selected or merged automatically, but it
                    # also must not prevent independent missing clips from
                    # finishing.  The next scan excludes rejected clips and
                    # continues the rest of the episode; once no runnable work
                    # remains, ``quality_blocked`` below requests one human
                    # decision for the quarantined results.
                    self.review_quality(production_id)
                    continue

                quality_blocked = self._quality_blocked_rows(overview, segments)
                quality_blocked_ids = {row.get("segment_id") for row in quality_blocked}
                target = next((segment for segment in segments
                               if (segment["id"] not in completed_ids
                                    or segment.get("status") != "ready"
                                    or not segment.get("video_prompt", "").strip()
                                    or bool(segment.get("stale_reasons")))
                               and segment["id"] not in quality_blocked_ids), None)
                if target is not None:
                    self._process_segment(production_id, target, overview)
                    continue
                if quality_blocked:
                    raise RuntimeError(self._quality_attention_message(quality_blocked))
                if self.review_quality is not None:
                    self._update(production_id, status="running", stage="quality",
                                 current_segment_id=None, current_index=0,
                                 run_id=None, request_id=None,
                                 stage_detail="Reviewing completed takes")
                    reviewed = self.review_quality(production_id)
                    failed = reviewed.get("failed", []) if isinstance(reviewed, dict) else []
                    if failed:
                        raise RuntimeError(self._quality_attention_message(failed))
                if production.get("automation", {}).get("merge", True):
                    self._update(production_id, status="running", stage="merge",
                                 current_segment_id=None, current_index=0,
                                 run_id=None, request_id=None,
                                 stage_detail="Assembling final film")
                    self.build_film(production_id)
                self._update(production_id, status="completed", stage="completed",
                             completed=len(segments), total=len(segments),
                             current_segment_id=None, current_index=0,
                             run_id=None, request_id=None, attempt=0,
                             stage_detail="Episode complete", last_error="",
                             finished_at=time.time())
                return
        except Exception as exc:
            message = str(exc)[:2000]
            # Shutdown is recoverable: leave the durable cursor active so the
            # next Studio process restarts it. All ordinary failures stop at a
            # visible attention state rather than looping destructively.
            if self.stopping.is_set():
                self._update(production_id, status="running", last_error=message,
                             stage_detail="Waiting for Studio restart")
            else:
                self._update(production_id, status="needs_attention",
                             last_error=message, stage_detail="Needs attention",
                             finished_at=time.time())
        finally:
            with self.lock:
                current = self.workers.get(production_id)
                if current is threading.current_thread():
                    self.workers.pop(production_id, None)
