"""Public Hermes-tool bridge from Talk to Nerve.

The boundary is deliberate: this module never imports ``hermes_nerve`` and never
reads Nerve's SQLite files. Every operation crosses Hermes' public
``PluginContext.dispatch_tool`` surface, so Hermes remains the authority and Nerve
remains independently replaceable.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from . import talk_host
except ImportError:  # pragma: no cover - Talk's flat-module install
    import talk_host


MAX_BRIDGE_OUTPUT_CHARS = 4_000
NERVE_PLUGIN_ID = "nerve"


class NerveBridgeError(RuntimeError):
    """Nerve is unavailable, refused a call, or returned a malformed receipt."""


def _ctx():
    ctx = talk_host.get_ctx()
    if ctx is None:
        raise NerveBridgeError("Talk is not attached to a Hermes plugin context")
    return ctx


def available() -> bool:
    """Return whether this Talk process can safely dispatch to a loaded Nerve plugin."""

    try:
        ctx = _ctx()
    except NerveBridgeError:
        return False
    if not callable(getattr(ctx, "dispatch_tool", None)):
        return False
    has_plugin = getattr(ctx, "has_plugin", None)
    if callable(has_plugin):
        try:
            return bool(has_plugin(NERVE_PLUGIN_ID))
        except Exception:  # noqa: BLE001 - an availability probe must fail closed
            return False
    # Older compatible hosts may expose dispatch_tool without has_plugin. The
    # actual dispatch still fails closed and returns no fabricated state.
    return True


def _dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    ctx = _ctx()
    dispatch = getattr(ctx, "dispatch_tool", None)
    if not callable(dispatch):
        raise NerveBridgeError("this Hermes host does not expose PluginContext.dispatch_tool")
    has_plugin = getattr(ctx, "has_plugin", None)
    if callable(has_plugin):
        try:
            if not has_plugin(NERVE_PLUGIN_ID):
                raise NerveBridgeError("the Nerve plugin is not loaded in this Hermes profile")
        except NerveBridgeError:
            raise
        except Exception as exc:
            # Sanitize the plugin-boundary failure: never leak a host stack
            # or internals into a spoken or logged receipt.
            raise NerveBridgeError("could not prove that Nerve is loaded") from exc

    try:
        raw = dispatch(tool_name, arguments)
    except Exception as exc:
        raise NerveBridgeError(f"Hermes refused {tool_name}: {type(exc).__name__}") from exc
    if isinstance(raw, dict):
        body = raw
    elif isinstance(raw, str):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NerveBridgeError(f"{tool_name} returned non-JSON output") from exc
    else:
        raise NerveBridgeError(f"{tool_name} returned an unsupported receipt type")
    if not isinstance(body, dict):
        raise NerveBridgeError(f"{tool_name} returned a non-object JSON receipt")
    if body.get("ok") is not True:
        error = str(body.get("error") or "Nerve rejected the request")
        raise NerveBridgeError(error[:500])
    return body


def _bounded_json(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    if len(rendered) <= MAX_BRIDGE_OUTPUT_CHARS:
        return rendered
    # Never tail-truncate JSON. Return a valid, explicit summary instead.
    return json.dumps(
        {
            "truncated": True,
            "message": "Nerve returned more structured state than Talk can safely read aloud.",
            "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
        },
        sort_keys=True,
    )


def status(task_id: str) -> dict[str, Any]:
    value = task_id.strip()
    if not value:
        raise NerveBridgeError("task_id is required for Nerve status")
    return _dispatch("nerve_work_status", {"task_id": value})


def status_text(task_id: str) -> str:
    return _bounded_json(status(task_id))


def assess(
    task_id: str,
    *,
    trigger: str = "voice_operator",
    failures: list[str] | None = None,
) -> dict[str, Any]:
    if not task_id.strip():
        raise NerveBridgeError("task_id is required for an explicit Nerve assessment")
    return _dispatch(
        "nerve_supervise_card",
        {
            "action": "assess_trajectory",
            "task_id": task_id.strip(),
            "trigger": trigger or "voice_operator",
            "failures": [str(item) for item in (failures or [])][:16],
        },
    )


def assess_text(
    task_id: str,
    *,
    trigger: str = "voice_operator",
    failures: list[str] | None = None,
) -> str:
    return _bounded_json(assess(task_id, trigger=trigger, failures=failures))


def request_review(task_id: str, *, summary: str, reviewer: str = "") -> dict[str, Any]:
    """Request canonical Hermes review; this is intentionally not a kill primitive."""

    if not task_id.strip():
        raise NerveBridgeError("task_id is required for review handoff")
    if not summary.strip():
        raise NerveBridgeError("a short review summary is required")
    args: dict[str, Any] = {
        "action": "request_review",
        "task_id": task_id.strip(),
        "summary": summary.strip()[:1_500],
    }
    if reviewer.strip():
        args["reviewer"] = reviewer.strip()
    return _dispatch("nerve_supervise_card", args)


def request_review_text(task_id: str, *, summary: str, reviewer: str = "") -> str:
    return _bounded_json(request_review(task_id, summary=summary, reviewer=reviewer))


def explain(task_id: str) -> str:
    """Return bounded evidence-backed state for the voice model to summarize.

    This performs no extra model call. It deliberately exposes the structured
    Nerve receipt rather than inventing an interpretation in the bridge.
    """

    if not task_id.strip():
        raise NerveBridgeError("task_id is required to explain a supervised run")
    return status_text(task_id.strip())


def remote_delegate(
    *,
    host: str,
    goal: str,
    context: str = "",
    workspace: str = "",
    profile: str = "",
    max_turns: int | None = None,
    task_id: str = "",
    require_claim_fence: bool = False,
) -> dict[str, Any]:
    if not host.strip() or not goal.strip():
        raise NerveBridgeError("remote delegation requires an admin-configured host alias and goal")
    args: dict[str, Any] = {
        "host": host.strip(),
        "goal": goal.strip(),
        "context": context,
        "require_claim_fence": bool(require_claim_fence),
    }
    if workspace.strip():
        args["workspace"] = workspace.strip()
    if profile.strip():
        args["profile"] = profile.strip()
    if max_turns is not None:
        args["max_turns"] = int(max_turns)
    if task_id.strip():
        args["task_id"] = task_id.strip()
    return _dispatch("nerve_remote_delegate_task", args)


def remote_status(job_id: str) -> dict[str, Any]:
    return _dispatch("nerve_remote_worker_status", {"job_id": job_id})


def remote_result(job_id: str, *, wait_seconds: float = 0.0) -> dict[str, Any]:
    args: dict[str, Any] = {"job_id": job_id}
    if wait_seconds > 0:
        args["wait_seconds"] = min(float(wait_seconds), 120.0)
    return _dispatch("nerve_remote_worker_result", args)


def remote_cancel(job_id: str) -> dict[str, Any]:
    return _dispatch("nerve_remote_worker_cancel", {"job_id": job_id})


def remote_control(
    job_id: str,
    *,
    control: str,
    decision_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    value = control.strip().upper()
    if value not in {"WATCH", "REPLAN", "BLOCK", "STOP_REQUESTED"}:
        raise NerveBridgeError("unsupported Nerve remote control")
    return _dispatch(
        "nerve_remote_worker_control",
        {
            "job_id": job_id,
            "control": value,
            "decision_id": decision_id,
            "reason": reason[:1_000],
        },
    )


__all__ = [
    "MAX_BRIDGE_OUTPUT_CHARS",
    "NERVE_PLUGIN_ID",
    "NerveBridgeError",
    "assess",
    "assess_text",
    "available",
    "explain",
    "remote_cancel",
    "remote_control",
    "remote_delegate",
    "remote_result",
    "remote_status",
    "request_review",
    "request_review_text",
    "status",
    "status_text",
]
