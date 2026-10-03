"""Optional Hermes TaskWorkerProvider adapter backed by Nerve SSH workers.

This module only activates on Hermes builds that expose the generic
``agent.task_worker_provider`` contract *and* ``PluginContext`` worker-provider
registration. Unsupported hosts return no provider rather than falling back to
ad-hoc SSH from the voice layer.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from . import talk_nerve
except ImportError:  # pragma: no cover - Talk flat-module install
    import talk_nerve

WORKER_NAME = "hermes-talk-nerve-ssh"
_TERMINAL = frozenset(
    {"completed", "failed", "cancelled", "lost", "fenced", "rate_limited", "handed_off"}
)


@dataclass(frozen=True)
class NerveWorkerConfig:
    host: str
    workspace: str = ""
    profile: str = ""
    max_turns: int | None = None
    require_claim_fence: bool = False
    poll_seconds: float = 0.5
    cancel_wait_seconds: float = 10.0

    @classmethod
    def from_context(cls, ctx) -> NerveWorkerConfig:
        raw = ctx.get_config("nerve_worker", {})
        if not isinstance(raw, dict):
            raise ValueError("nerve_worker must be an object")
        host = str(raw.get("host") or "").strip()
        if not host:
            raise ValueError("nerve_worker.host is required")
        turns = raw.get("max_turns")
        if turns in (None, ""):
            turns = None
        else:
            turns = int(turns)
            if turns < 1:
                raise ValueError("nerve_worker.max_turns must be positive")
        poll = float(raw.get("poll_seconds") or 0.5)
        cancel_wait = float(raw.get("cancel_wait_seconds") or 10.0)
        return cls(
            host=host,
            workspace=str(raw.get("workspace") or "").strip(),
            profile=str(raw.get("profile") or "").strip(),
            max_turns=turns,
            require_claim_fence=bool(raw.get("require_claim_fence", False)),
            poll_seconds=max(0.2, min(poll, 5.0)),
            cancel_wait_seconds=max(0.0, min(cancel_wait, 60.0)),
        )


def _job_id(receipt: dict[str, Any]) -> str:
    job = receipt.get("job") if isinstance(receipt.get("job"), dict) else {}
    value = str(job.get("job_id") or job.get("id") or "").strip()
    if not value:
        raise ValueError("Nerve remote delegation returned no job_id")
    return value


def _result_from_job(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "unknown").lower()
    mapped = {
        "completed": "completed",
        "cancelled": "cancelled",
        "failed": "failed",
        "fenced": "failed",
        "rate_limited": "failed",
        # These are terminal in Nerve, but they do not prove a failed child
        # result. Preserve uncertainty instead of turning absence of evidence
        # into a failure claim.
        "lost": "unknown",
        "handed_off": "unknown",
    }.get(status, "unknown")
    error = None
    if mapped != "completed":
        error = str(job.get("error_message") or job.get("error_code") or "").strip()
        if not error:
            error = f"nerve_remote_{status or 'unknown'}"
    return {
        "status": mapped,
        "output": str(job.get("result_text") or ""),
        "artifacts": [],
        "error": error,
    }


def build_provider(ctx):
    try:
        from agent.task_worker_provider import TaskWorkerProvider, TaskWorkerSession
    except ImportError:
        return None

    class Session(TaskWorkerSession):
        def __init__(self, request, config: NerveWorkerConfig):
            self.request = request
            self.config = config
            self.cancelled = threading.Event()
            self.job_id: str | None = None

        def _authorized(self) -> bool:
            return bool(self.request.still_authorized()) and not self.cancelled.is_set()

        def run(self):
            if not self._authorized():
                raise ValueError("Task worker owner is not current")
            task_id = str(getattr(self.request, "task_id", "") or "")
            receipt = talk_nerve.remote_delegate(
                host=self.config.host,
                goal=str(self.request.goal),
                context=str(getattr(self.request, "context", "") or ""),
                workspace=self.config.workspace,
                profile=self.config.profile,
                max_turns=self.config.max_turns,
                task_id=task_id,
                # A provider request is already canonical task work. When a
                # Nerve task id is also available, require the run fence by
                # default unless the operator explicitly configured otherwise.
                require_claim_fence=self.config.require_claim_fence or bool(task_id),
            )
            self.job_id = _job_id(receipt)
            self.request.report({"status": "running"})

            while self._authorized():
                status_receipt = talk_nerve.remote_status(self.job_id)
                job = (
                    status_receipt.get("job")
                    if isinstance(status_receipt.get("job"), dict)
                    else {}
                )
                state = str(job.get("status") or "").lower()
                if state in _TERMINAL:
                    return _result_from_job(job)
                time.sleep(self.config.poll_seconds)

            # Ownership retirement and explicit cancellation both target only
            # this exact Nerve job. Do not claim cancellation until Nerve
            # reports a terminal artifact; an accepted cancel request is not
            # the same thing as a stopped remote worker.
            terminal = self._cancel_and_confirm() if self.job_id else None
            if not self.request.still_authorized():
                raise ValueError("Task worker owner was retired")
            if terminal is not None:
                return _result_from_job(terminal)
            return {
                "status": "unknown",
                "output": "",
                "artifacts": [],
                "error": "remote_cancel_unconfirmed",
            }

        def _cancel_and_confirm(self):
            assert self.job_id is not None
            try:
                receipt = talk_nerve.remote_cancel(self.job_id)
            except talk_nerve.NerveBridgeError:
                return None
            job = receipt.get("job") if isinstance(receipt.get("job"), dict) else {}
            if str(job.get("status") or "").lower() in _TERMINAL:
                return job
            deadline = time.monotonic() + self.config.cancel_wait_seconds
            while time.monotonic() < deadline:
                try:
                    receipt = talk_nerve.remote_status(self.job_id)
                except talk_nerve.NerveBridgeError:
                    return None
                job = receipt.get("job") if isinstance(receipt.get("job"), dict) else {}
                if str(job.get("status") or "").lower() in _TERMINAL:
                    return job
                time.sleep(self.config.poll_seconds)
            return None

        def cancel(self):
            # Signal the run loop. The loop owns confirmation so a cancellation
            # result is emitted only after an exact terminal receipt.
            self.cancelled.set()

        def steering(self):
            # Nerve's current remote control protocol can request WATCH/REPLAN/
            # BLOCK/STOP, but it does not carry arbitrary steering text with
            # delivery receipts. Claiming Hermes steer support would be false.
            return {"supported": False, "reason": "nerve_remote_text_steering_not_yet_bridged"}

        def steer(self, text, *, action_id, expected_session_id, expected_turn_id):
            del text, action_id, expected_session_id, expected_turn_id
            return "rejected"

        def approvals(self):
            # Current Nerve remote API does not expose remote Hermes approval
            # requests. Fail closed rather than claiming none were required.
            return []

        def approve(self, request_id, choice):
            del request_id, choice
            raise ValueError("Nerve SSH worker approvals are not bridged by this adapter")

    class Provider(TaskWorkerProvider):
        name = WORKER_NAME

        def configuration(self):
            return NerveWorkerConfig.from_context(ctx)

        def available(self):
            try:
                self.configuration()
                return talk_nerve.available()
            except (ValueError, talk_nerve.NerveBridgeError):
                return False

        def open(self, request):
            if not request.still_authorized():
                raise ValueError("Task worker owner is not current")
            return Session(request, self.configuration())

    return Provider()


__all__ = ["WORKER_NAME", "NerveWorkerConfig", "build_provider"]
