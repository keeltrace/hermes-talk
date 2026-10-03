"""Deterministic speech policy for Nerve supervision events.

It decides *whether* a supervision fact deserves the operator's ear. It never
executes work, changes Nerve state, or trusts arbitrary worker prose.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

QUIET = "quiet"
NOTIFY = "notify"
URGENT = "urgent"
LEVELS = frozenset({QUIET, NOTIFY, URGENT})

# Nerve decisions/control values that are useful to a human. WATCH is
# intentionally quiet: normal supervision should feel ambient, not chatty.
_URGENT_CONTROLS = frozenset({"BLOCK", "STOP_REQUESTED", "ORCH_REVIEW"})
_NOTIFY_CONTROLS = frozenset({"REPLAN"})
_TERMINAL_FAILURES = frozenset(
    {"failed", "cancelled", "lost", "fenced", "rate_limited", "handed_off"}
)
_TERMINAL_SUCCESS = frozenset({"completed"})


@dataclass(frozen=True)
class SpeechDecision:
    level: str
    reason: str
    key: str
    speak: bool
    interrupt: bool
    summary: str


def _string(value: Any) -> str:
    return str(value or "").strip()


def _safe_summary(text: str, *, limit: int = 280) -> str:
    """Bound synthesized control-plane text; never pass through worker output."""

    clean = " ".join(_string(text).split())
    return clean[:limit]


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Project several Nerve receipt shapes into one small speech contract."""

    body = payload if isinstance(payload, dict) else {}
    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    assessment = body.get("assessment") if isinstance(body.get("assessment"), dict) else {}
    control = body.get("control") if isinstance(body.get("control"), dict) else {}
    job = body.get("job") if isinstance(body.get("job"), dict) else {}
    result = body.get("result") if isinstance(body.get("result"), dict) else {}

    # Work-status often nests control/budget/projection inside status. Keep the
    # parser permissive, but only lift known scalar control-plane facts.
    status_control = status.get("control") if isinstance(status.get("control"), dict) else {}
    picked_control = control or status_control
    projection = status.get("projection") if isinstance(status.get("projection"), dict) else {}
    budget = status.get("budget") if isinstance(status.get("budget"), dict) else {}
    nerve = status.get("nerve") if isinstance(status.get("nerve"), dict) else {}

    state = _string(
        job.get("status")
        or result.get("status")
        or status.get("state")
        or status.get("status")
    ).lower()
    control_value = _string(
        picked_control.get("control")
        or nerve.get("level")
        or assessment.get("control")
        or assessment.get("decision")
        or assessment.get("level")
    ).upper()
    confidence = picked_control.get(
        "confidence", nerve.get("confidence", assessment.get("confidence"))
    )
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None

    verified = projection.get(
        "verified_required", projection.get("verified_count", status.get("verified_count"))
    )
    total = projection.get(
        "required_total", projection.get("total_count", status.get("total_count"))
    )
    extension = nerve.get(
        "extension_tokens", budget.get("extension_tokens", status.get("extension_tokens"))
    )
    review = bool(
        status.get("orchestrator_review_required")
        or status.get("human_required")
        or nerve.get("requires_orchestrator_review")
        or picked_control.get("orchestrator_review_required")
        or picked_control.get("requires_orchestrator_review")
        or body.get("transitioned") is True
    )
    contradiction = bool(
        status.get("dod_contradiction")
        or status.get("completion_contradiction")
        or assessment.get("dod_contradiction")
    )
    blocker = bool(status.get("blocked") or assessment.get("blocked"))

    return {
        "task_id": _string(status.get("task_id") or job.get("task_id") or result.get("task_id")),
        "state": state,
        "control": control_value,
        "confidence": confidence,
        "verified": verified,
        "total": total,
        "extension_tokens": extension,
        "event_type": _string(
            body.get("event_type") or body.get("kind") or body.get("action")
        ).lower(),
        "review_required": review,
        "contradiction": contradiction,
        "blocked": blocker,
    }


def classify(payload: dict[str, Any]) -> SpeechDecision:
    fact = normalize(payload)
    control = fact["control"]
    state = fact["state"]
    task = fact["task_id"] or "supervised task"

    level, reason, summary = QUIET, "routine_progress", ""
    if fact["contradiction"]:
        level, reason = URGENT, "dod_contradiction"
        summary = (
            "Nerve found a completion claim that conflicts with the verified "
            f"Definition of Done for {task}."
        )
    elif fact["review_required"]:
        level, reason = URGENT, "review_required"
        summary = f"Nerve needs canonical Hermes review for {task}."
    elif control in _URGENT_CONTROLS:
        level, reason = URGENT, "control_escalation"
        summary = f"Nerve escalated {task} to {control}; Hermes still owns the final decision."
    elif fact["blocked"]:
        level, reason = URGENT, "worker_blocked"
        summary = f"Nerve reports that {task} is blocked and needs attention."
    elif state in _TERMINAL_FAILURES:
        level, reason = URGENT, "terminal_failure"
        summary = f"The supervised worker for {task} ended with status {state}."
    elif control in _NOTIFY_CONTROLS:
        level, reason = NOTIFY, "replan_recommended"
        summary = f"Nerve recommends replanning {task}; Hermes owns the decision."
    elif state in _TERMINAL_SUCCESS:
        level, reason = NOTIFY, "terminal_success"
        summary = f"The supervised worker for {task} completed."
    elif fact["extension_tokens"] and fact["event_type"] in {
        "nerve_budget_extension", "budget_extension", "extension_granted"
    }:
        level, reason = NOTIFY, "bounded_extension"
        summary = f"Nerve granted the configured bounded budget extension for {task}."

    safe = _safe_summary(summary)
    # Dedupe on the semantic notification, not every changing progress field.
    # A REPLAN notice should not repeat merely because verified_percent moved.
    material = json.dumps(
        {
            "reason": reason,
            "task_id": fact["task_id"],
            "state": state if reason.startswith("terminal_") else "",
            "control": control if reason in {"control_escalation", "replan_recommended"} else "",
            "extension_tokens": fact["extension_tokens"] if reason == "bounded_extension" else 0,
        },
        sort_keys=True,
        default=str,
    )
    key = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return SpeechDecision(
        level=level,
        reason=reason,
        key=key,
        speak=level != QUIET,
        interrupt=level == URGENT,
        summary=safe,
    )


class SpeechGate:
    """Process-local dedupe/cooldown gate for non-terminal ambient updates."""

    def __init__(self, *, cooldown_s: float = 45.0, now=time.monotonic) -> None:
        self.cooldown_s = max(float(cooldown_s), 0.0)
        self.now = now
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def admit(self, decision: SpeechDecision) -> bool:
        if not decision.speak:
            return False
        current = self.now()
        with self._lock:
            previous = self._seen.get(decision.key)
            if previous is not None and current - previous < self.cooldown_s:
                return False
            self._seen[decision.key] = current
            # Bound memory without introducing a cleanup thread.
            if len(self._seen) > 512:
                cutoff = current - max(self.cooldown_s * 4, 300.0)
                self._seen = {key: ts for key, ts in self._seen.items() if ts >= cutoff}
        return True


__all__ = [
    "LEVELS",
    "NOTIFY",
    "QUIET",
    "URGENT",
    "SpeechDecision",
    "SpeechGate",
    "classify",
    "normalize",
]
