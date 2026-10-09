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
    # Old or manually edited prompts are checked again at the final submission
    # boundary.  A failed text-only gate is safe to repair by rebuilding only
    # the prompt; no video has been submitted yet.
    "failed pre-render prompt quality",
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
    "did not respond in time",
    "not reachable", "temporarily unavailable", "winerror 10054",
    "winerror 5", "permissionerror", "ai is still working",
    "a video request is already active",
    "running or queued work", "already generating another video",
    "has not released gpu memory", "model has not released",
)
GLOBAL_FAILURE_MARKERS = (
    # Local services, shared workflow dependencies and filesystem failures
    # affect every remaining clip; skipping individual clips would only waste
    # time and repeat the same failure across the episode.
    "studio is shutting down",
    "comfyui is not reachable", "comfyui is not running",
    "confirm h3 studio and comfyui", "check that comfyui",
    "does not have the required lora", "required lora_name",
    "select the h3 desktop installation", "workflow model is missing",
    "connection refused", "connecttimeout", "readtimeout", "timed out",
    "winerror 10054", "a video request is already active",
    "submission is uncertain", "running or queued work",
    "already generating another video", "has not released gpu memory",
    "model has not released", "permissionerror", "winerror 5",
    "no space left", "disk full", "read-only file system",
    "cuda out of memory", "out of memory", "safetensors",
    "lmstudioerror: lm studio returned http 500",
    "ollama returned http 500", "ollama is not reachable",
    "cannot reach ollama", "cannot reach lm studio",
    "selected ollama model is not installed",
    "selected model is not loaded",
    "assistant load could not be confirmed",
    "comfyui queue state could not be verified",
    "comfyui's queue status could not be verified",
    "comfyui’s queue status could not be verified",
    "original comfyui connection is unknown",
    "comfyui transfer did not finish",
    "check the comfyui and lm studio connections",
    # These are compiler-owned invariants, not scene-authoring defects. If one
    # is absent after compilation, every following clip would fail for the same
    # shared language/template bug; do not spend an episode isolating them one
    # by one.
    "failed final h3 prompt quality: the final h3 prompt must contain exactly one final audio override",
    "failed final h3 prompt quality: the final audio override does not match the locked dialogue count",
    "failed final h3 prompt quality: the final h3 prompt lost or duplicated its production continuity override",
)


def is_prompt_repairable(message: str) -> bool:
    value = str(message).casefold()
    return any(marker in value for marker in PROMPT_REPAIR_MARKERS)


def is_transient_failure(message: str) -> bool:
    value = str(message).casefold()
    return any(marker in value for marker in TRANSIENT_MARKERS)


def is_global_failure(message: str) -> bool:
    """Return whether continuing other clips would repeat a shared failure.

    This function is called only after the prompt/render helper has exhausted
    its own bounded retry policy.  A still-transient transport or lock failure
    at that boundary is therefore shared infrastructure trouble, not evidence
    that one storyboard clip is defective.
    """
    value = str(message).casefold()
    return (any(marker in value for marker in GLOBAL_FAILURE_MARKERS)
            or is_transient_failure(value))


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

    def start(self, production_id, *, merge=True, review=False, requested_by="user"):
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
                merge=bool(merge), review=bool(review), requested_by=requested_by, started_at=started_at,
                finished_at=None, last_error="", blocked_segments=[],
                stage_detail="Checking missing work",
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
    def _blocked_attention_message(rows):
        labels = []
        for row in rows[:8]:
            index = row.get("index")
            title = str(row.get("title") or "").strip()
            reason = str(row.get("reason") or "").strip()
            label = (f"{index} {title}" if index else
                     str(row.get("segment_id") or "")[:8]).strip()
            labels.append(f"{label}: {reason}" if reason else label)
        suffix = " | ".join(labels)
        if len(rows) > len(labels):
            suffix += f" | +{len(rows) - len(labels)} more"
        return (
            f"Finished every independent clip that could continue. {len(rows)} clip(s) were "
            "isolated instead of stopping the episode. Review or edit those storyboard clips, "
            "then resume to retry only the unresolved work: " + suffix)

    def _block_segment(self, production_id, segment, stage, message):
        """Persist one clip-local failure and let the same episode continue."""
        production = self.get_production(production_id)
        automation = production.get("automation", {})
        rows = [row for row in automation.get("blocked_segments", [])
                if row.get("segment_id") != segment.get("id")]
        rows.append({
            "segment_id": segment.get("id"),
            "index": int(segment.get("index") or 0),
            "title": str(segment.get("title") or "")[:120],
            "stage": stage if stage in ("prompts", "videos") else "prompts",
            "reason": str(message)[:1600],
            "blocked_at": time.time(),
        })
        self._update(
            production_id, status="running", stage="scan",
            current_segment_id=None, current_index=0, run_id=None,
            request_id=None, attempt=0, last_error="",
            blocked_segments=rows,
            stage_detail=f"Isolated clip {segment.get('index')}; continuing remaining clips",
        )
        return rows

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

    @staticmethod
    def _prompt_needs_build(segment):
        quality = segment.get("prompt_quality") or {}
        return (
            segment.get("status") != "ready"
            or not str(segment.get("video_prompt") or "").strip()
            or bool(segment.get("stale_reasons"))
            or quality.get("status") == "failed"
        )

    def _prepare_prompt(self, production_id, segment):
        """Build one prompt during the prompt-only pass before video rendering.

        Keeping prompt preparation separate from rendering avoids repeatedly
        switching the local LLM and ComfyUI workload for every clip.  It also
        makes the progress UI truthful: every missing prompt is checked before
        the continuous render pass starts.
        """
        segment_id, index = segment["id"], segment["index"]
        repaired = False
        max_attempts = len(self.retry_delays) + 1
        for attempt in range(max_attempts):
            if self._pause_if_requested(production_id):
                return
            self._update(
                production_id, status="running", stage="prompts",
                current_segment_id=segment_id, current_index=index,
                run_id=None, request_id=None, attempt=attempt,
                stage_detail=f"Building prompt for clip {index}",
            )
            try:
                self.generate_prompt(production_id, segment_id)
                current = next((item for item in self.get_production(production_id).get("segments", [])
                                if item.get("id") == segment_id), None)
                if current is None:
                    raise RuntimeError("The queued production clip no longer exists.")
                if self._prompt_needs_build(current):
                    raise RuntimeError(
                        f"Clip {index} prompt generation finished without a usable reviewed prompt.")
                self._update(production_id, attempt=0, last_error="",
                             stage_detail=f"Prompt ready for clip {index}")
                return
            except Exception as exc:
                message = str(exc)
                if is_prompt_repairable(message) and not repaired:
                    repaired = True
                    self._update(production_id, last_error=message[:2000],
                                 stage_detail=f"Repairing prompt for clip {index}")
                    continue
                if attempt + 1 < max_attempts and is_transient_failure(message):
                    delay = self.retry_delays[attempt]
                    self._update(production_id, status="retrying", stage="prompts",
                                 attempt=attempt + 1, last_error=message[:2000],
                                 stage_detail=f"Retrying prompt {index} in {int(delay)}s")
                    if self._wait(delay):
                        raise RuntimeError("Studio is shutting down; the task will resume next time.")
                    continue
                raise
        raise RuntimeError(f"Clip {index} prompt exceeded its safe retry limit.")

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
                automation = production.get("automation", {})
                review_after_render = bool(automation.get("review", False))
                blocked_rows = list(automation.get("blocked_segments", []))
                blocked_ids = {row.get("segment_id") for row in blocked_rows}
                self._update(production_id, status="running", total=len(segments),
                             completed=len(completed_ids), last_error="")
                # If Studio restarted while a video request was active, finish
                # resolving that durable request before touching the LLM.
                if automation.get("run_id") or automation.get("request_id"):
                    active_segment = next((segment for segment in segments
                                           if segment.get("id") == automation.get("current_segment_id")), None)
                    if active_segment is not None:
                        try:
                            self._process_segment(production_id, active_segment, overview)
                        except Exception as exc:
                            if is_global_failure(str(exc)):
                                raise
                            self._block_segment(
                                production_id, active_segment, "videos", str(exc))
                        continue

                quality_blocked = self._quality_blocked_rows(overview, segments)
                quality_blocked_ids = {row.get("segment_id") for row in quality_blocked}

                # Phase 1: prepare and preflight every missing prompt.  No new
                # video is submitted until this pass is complete.
                prompt_candidates = [
                    segment for segment in segments
                    if segment.get("id") not in quality_blocked_ids
                    and segment.get("id") not in blocked_ids
                    and (segment.get("id") not in completed_ids
                         or segment.get("status") != "ready"
                         or bool(segment.get("stale_reasons")))
                    and self._prompt_needs_build(segment)
                ]
                prompt_target = next((segment for segment in prompt_candidates
                                      if segment.get("quality_repair_direction")), None)
                if prompt_target is None and prompt_candidates:
                    prompt_target = prompt_candidates[0]
                if prompt_target is not None:
                    try:
                        self._prepare_prompt(production_id, prompt_target)
                    except Exception as exc:
                        if is_global_failure(str(exc)):
                            raise
                        self._block_segment(
                            production_id, prompt_target, "prompts", str(exc))
                    continue

                # An explicitly approved quality repair is the only rejected
                # clip the queue may regenerate.  Prioritise it so an earlier
                # unapproved rejection cannot hijack the resumed worker.
                target = next((segment for segment in segments
                               if segment.get("quality_repair_direction") and
                               segment.get("id") not in blocked_ids and
                               (segment["id"] not in completed_ids
                                or segment.get("status") != "ready"
                                or not segment.get("video_prompt", "").strip()
                                or bool(segment.get("stale_reasons")))), None)
                if target is not None:
                    try:
                        self._process_segment(production_id, target, overview)
                    except Exception as exc:
                        if is_global_failure(str(exc)):
                            raise
                        self._block_segment(production_id, target, "videos", str(exc))
                    continue

                # Newly rendered takes must receive their review before older
                # rejected clips pause the queue again.
                if (review_after_render and self.review_quality is not None and
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

                target = next((segment for segment in segments
                               if (segment["id"] not in completed_ids
                                    or segment.get("status") != "ready"
                                    or not segment.get("video_prompt", "").strip()
                                    or bool(segment.get("stale_reasons")))
                               and segment["id"] not in quality_blocked_ids
                               and segment["id"] not in blocked_ids), None)
                if target is not None:
                    try:
                        self._process_segment(production_id, target, overview)
                    except Exception as exc:
                        if is_global_failure(str(exc)):
                            raise
                        self._block_segment(production_id, target, "videos", str(exc))
                    continue
                # Clip-local structural/prompt/render failures are collected
                # only after all independent work has finished. They do not
                # permit a partial final assembly, but they also never stop an
                # unrelated clip from reaching a usable take.
                if blocked_rows:
                    raise RuntimeError(self._blocked_attention_message(blocked_rows))
                # A take rejected by an explicitly requested QC pass always
                # needs a human decision.  Resuming the ordinary generation
                # queue must neither rerender it nor attempt to assemble a cut
                # with a missing adopted take.
                if quality_blocked:
                    raise RuntimeError(self._quality_attention_message(quality_blocked))
                if review_after_render and self.review_quality is not None:
                    self._update(production_id, status="running", stage="quality",
                                 current_segment_id=None, current_index=0,
                                 run_id=None, request_id=None,
                                 stage_detail="Reviewing completed takes")
                    reviewed = self.review_quality(production_id)
                    failed = reviewed.get("failed", []) if isinstance(reviewed, dict) else []
                    if failed:
                        raise RuntimeError(self._quality_attention_message(failed))
                # Assembly belongs to the normal generation path, not to
                # optional post-render QC.  This keeps the durable worker able
                # to create the initial cut even when the browser is closed.
                # When a user-approved repair replaces a take, the same path
                # assembles the updated cut from the new signature.
                if automation.get("merge", True):
                    self._update(production_id, status="running", stage="merge",
                                 current_segment_id=None, current_index=0,
                                 run_id=None, request_id=None,
                                 stage_detail=("Assembling reviewed film" if review_after_render else
                                               "Assembling initial cut"))
                    self.build_film(production_id)
                detail = ("Reviewed film complete" if review_after_render else
                          "Initial cut ready; optional quality review is available")
                self._update(production_id, status="completed", stage="completed",
                             completed=len(segments), total=len(segments),
                             current_segment_id=None, current_index=0,
                             run_id=None, request_id=None, attempt=0,
                              stage_detail=detail, last_error="",
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
