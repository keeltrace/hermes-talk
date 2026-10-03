"""Curated Nerve tools for the realtime Talk model."""

from __future__ import annotations

try:
    from . import talk_nerve
except ImportError:  # pragma: no cover - Talk flat-module install
    import talk_nerve


_TOOL_STATUS = {
    "type": "function",
    "name": "nerve_status",
    "description": (
        "Read evidence-backed Nerve supervision state for an exact task. Use this for "
        "progress, verified Definition-of-Done status, budget/control state, and "
        "'what is actually verified?'. "
        "It does not change the worker."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Exact canonical Hermes/Kanban task id. Never guess an id.",
            }
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
}

_TOOL_EXPLAIN = {
    "type": "function",
    "name": "nerve_explain",
    "description": (
        "Fetch bounded structured Nerve evidence so you can explain why supervision is concerned. "
        "Summarize only the returned evidence; do not invent motives or hidden worker state."
    ),
    "parameters": {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    },
}

_TOOL_ASSESS = {
    "type": "function",
    "name": "nerve_assess",
    "description": (
        "Ask Nerve to assess the exact supervised task trajectory now. This may update Nerve's "
        "WATCH/REPLAN/review state, but Nerve never owns the final stop/continue decision."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "trigger": {
                "type": "string",
                "description": "Short operator reason, for example voice_operator or manual_check.",
            },
            "failures": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 16,
                "description": (
                    "Optional bounded known failure labels; never copy secrets or full logs."
                ),
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
}

_TOOL_REVIEW = {
    "type": "function",
    "name": "nerve_request_review",
    "description": (
        "Hand an exact supervised task to the canonical Hermes orchestrator/reviewer "
        "after Nerve has a complete checkpoint. This is NOT a stop or kill command. "
        "Use it when the operator explicitly asks "
        "for orchestrator review or accepts a Nerve escalation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "summary": {
                "type": "string",
                "description": (
                    "Short operator-owned reason for review; do not paste arbitrary worker output."
                ),
            },
            "reviewer": {"type": "string"},
        },
        "required": ["task_id", "summary"],
        "additionalProperties": False,
    },
}


def tool_schemas() -> list[dict]:
    if not talk_nerve.available():
        return []
    return [_TOOL_STATUS, _TOOL_EXPLAIN, _TOOL_ASSESS, _TOOL_REVIEW]


def _status(arguments: dict) -> str:
    return talk_nerve.status_text(str(arguments.get("task_id") or ""))


def _explain(arguments: dict) -> str:
    return talk_nerve.explain(str(arguments.get("task_id") or ""))


def _assess(arguments: dict) -> str:
    return talk_nerve.assess_text(
        str(arguments.get("task_id") or ""),
        trigger=str(arguments.get("trigger") or "voice_operator"),
        failures=[str(item) for item in (arguments.get("failures") or [])],
    )


def _review(arguments: dict) -> str:
    return talk_nerve.request_review_text(
        str(arguments.get("task_id") or ""),
        summary=str(arguments.get("summary") or ""),
        reviewer=str(arguments.get("reviewer") or ""),
    )


def handlers() -> dict[str, object]:
    return {
        "nerve_status": _status,
        "nerve_explain": _explain,
        "nerve_assess": _assess,
        "nerve_request_review": _review,
    }


__all__ = ["handlers", "tool_schemas"]
