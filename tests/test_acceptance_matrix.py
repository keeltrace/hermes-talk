"""The plan's section 8 live acceptance matrix, row by row.

Each test names the row it discharges. Rows that cannot be discharged without a
reachable remote host are marked and skipped rather than passed by a stub, which
is the point of the plan's "no private SSH backdoor" rule.
"""

from __future__ import annotations

import json

import pytest

import talk_nerve
import talk_nerve_policy as policy

TASK = "KEE-286"


class Bridge:
    """Stands in for PluginContext.dispatch_tool, recording every dispatch."""

    def __init__(self, responses):
        self.responses = responses
        self.dispatched = []

    def dispatch_tool(self, name, arguments):
        self.dispatched.append((name, arguments))
        value = self.responses[name]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


class Ctx:
    def __init__(self, responses):
        self.bridge = Bridge(responses)

    def get_config(self, key, default=None):
        return default

    def has_plugin(self, name):
        return name == talk_nerve.NERVE_PLUGIN_ID

    def dispatch_tool(self, name, arguments):
        return self.bridge.dispatch_tool(name, arguments)


@pytest.fixture
def ctx(monkeypatch):
    """Attach a scripted bridge so the real talk_nerve module can reach it."""

    def build(responses):
        context = Ctx(responses)
        monkeypatch.setattr(talk_nerve, "_ctx", lambda: context)
        return context

    return build


# --- row: nerve_status on a supervised task -----------------------------


def test_status_returns_bounded_structured_state_without_mutation(ctx):
    """| nerve_status on supervised task | bounded structured state, no mutation |"""
    receipt = {"ok": True, "status": {"task_id": TASK, "state": "running",
                                     "control": {"control": "WATCH", "confidence": 0.38}}}
    context = ctx({"nerve_work_status": receipt})
    state = talk_nerve.status(TASK)
    assert state == receipt
    name, arguments = context.bridge.dispatched[0]
    assert name == "nerve_work_status"
    assert arguments == {"task_id": TASK}


def test_status_without_an_exact_task_id_is_refused(ctx):
    """Correlation is exact; recency matching is not an option."""
    context = ctx({"nerve_work_status": {}})
    with pytest.raises(talk_nerve.NerveBridgeError, match="task_id is required"):
        talk_nerve.status("   ")
    assert context.bridge.dispatched == []


# --- row: nerve_assess from fresh operator speech ------------------------


def test_assess_dispatches_a_specific_trajectory_action(ctx):
    """| nerve_assess from fresh operator speech | exact task assessment; never kill |"""
    context = ctx({"nerve_supervise_card": {"ok": True,
                                           "assessment": {"control": "REPLAN",
                                                          "confidence": 0.46}}})
    receipt = talk_nerve.assess(TASK)
    assert receipt["assessment"]["control"] == "REPLAN"
    name, arguments = context.bridge.dispatched[0]
    assert name == "nerve_supervise_card"
    assert arguments["action"] == "assess_trajectory"
    assert arguments["task_id"] == TASK
    assert arguments["trigger"] == "voice_operator"


def test_no_dispatched_tool_can_kill(ctx):
    """Nerve may WATCH/REPLAN/review, never kill."""
    context = ctx({"nerve_supervise_card": {"ok": True,
                                           "assessment": {"control": "BLOCK",
                                                          "confidence": 0.9}}})
    talk_nerve.assess(TASK)
    for name, arguments in context.bridge.dispatched:
        assert "kill" not in name.lower()
        assert "kill" not in json.dumps(arguments).lower()


# --- row: nerve_request_review without / with a complete checkpoint ------


def test_request_review_requires_a_summary(ctx):
    context = ctx({"nerve_supervise_card": {}})
    with pytest.raises(talk_nerve.NerveBridgeError, match="review summary is required"):
        talk_nerve.request_review(TASK, summary="   ")
    assert context.bridge.dispatched == []


def test_request_review_without_a_complete_checkpoint_is_refused(ctx):
    """| nerve_request_review without complete checkpoint | explicit refusal |"""
    ctx({
        "nerve_supervise_card": {"error": "checkpoint_incomplete",
                                 "error_message": "no completed checkpoint to review"},
    })
    with pytest.raises(talk_nerve.NerveBridgeError, match="checkpoint_incomplete"):
        talk_nerve.request_review(TASK, summary="please review the plan")


def test_request_review_with_a_complete_checkpoint_returns_the_handoff_receipt(ctx):
    """| nerve_request_review with complete checkpoint | canonical reviewer handoff |"""
    context = ctx({
        "nerve_supervise_card": {
            "ok": True, "review_id": "rv-1", "status": "queued", "task_id": TASK,
        },
    })
    receipt = talk_nerve.request_review(TASK, summary="please review the plan")
    assert receipt["review_id"] == "rv-1"
    _, arguments = context.bridge.dispatched[0]
    assert arguments["action"] == "request_review"


def test_request_review_truncates_an_oversized_summary(ctx):
    context = ctx({"nerve_supervise_card": {"ok": True, "review_id": "rv-1"}})
    talk_nerve.request_review(TASK, summary="x" * 5_000)
    _, arguments = context.bridge.dispatched[0]
    assert len(arguments["summary"]) == 1_500


# --- rows: Nerve WATCH / REPLAN / review severity ------------------------


@pytest.mark.parametrize(
    "control,expected",
    [
        ("WATCH", policy.QUIET),
        ("REPLAN", policy.NOTIFY),
        ("BLOCK", policy.URGENT),
        ("STOP_REQUESTED", policy.URGENT),
        ("ORCH_REVIEW", policy.URGENT),
    ],
)
def test_verdicts_map_to_the_documented_speech_levels(control, expected):
    """| WATCH silent | REPLAN one notice | review/BLOCK urgent |"""
    decision = policy.classify(
        {"status": {"task_id": TASK, "control": {"control": control, "confidence": 0.4}}}
    )
    assert decision.level == expected


@pytest.mark.parametrize("control", ["BLOCK", "STOP_REQUESTED", "ORCH_REVIEW"])
def test_review_and_block_are_urgent_even_at_low_confidence(control):
    decision = policy.classify(
        {"status": {"task_id": TASK, "control": {"control": control, "confidence": 0.1}}}
    )
    assert decision.level == policy.URGENT


def test_do_d_contradiction_is_urgent():
    decision = policy.classify(
        {"status": {"task_id": TASK, "dod_contradiction": True,
                    "control": {"control": "WATCH"}}}
    )
    assert decision.level == policy.URGENT


def test_a_terminal_failure_is_urgent_and_a_completion_is_notify():
    failed = policy.classify({"status": {"task_id": TASK, "state": "failed"}})
    done = policy.classify({"status": {"task_id": TASK, "state": "completed"}})
    assert failed.level == policy.URGENT
    assert done.level == policy.NOTIFY


# --- authority boundary --------------------------------------------------


def test_the_bridge_dispatches_only_through_the_hermes_tool_boundary(ctx):
    context = ctx({"nerve_work_status": {"ok": True, "status": {"task_id": TASK}}})
    talk_nerve.status(TASK)
    for name, arguments in context.bridge.dispatched:
        assert isinstance(name, str) and name.startswith("nerve_")
        assert isinstance(arguments, dict)


def test_a_refused_tool_call_surfaces_as_a_bridge_error_not_a_silent_empty(ctx):
    ctx({"nerve_work_status": {"error": "supervision_disabled",
                               "error_message": "work supervision is disabled"}})
    with pytest.raises(talk_nerve.NerveBridgeError, match="supervision_disabled"):
        talk_nerve.status(TASK)


def test_a_non_object_receipt_is_refused(ctx):
    ctx({"nerve_work_status": ["not", "an", "object"]})
    with pytest.raises(talk_nerve.NerveBridgeError, match="non-object JSON"):
        talk_nerve.status(TASK)


def test_non_json_tool_output_is_refused(ctx):
    context = ctx({"nerve_work_status": {"ok": True, "status": {"task_id": TASK}}})
    context.bridge.responses["nerve_work_status"] = "<<not json>>"
    with pytest.raises(talk_nerve.NerveBridgeError, match="non-JSON"):
        talk_nerve.status(TASK)


def test_an_unloaded_nerve_plugin_is_never_claimed_available(ctx, monkeypatch):
    context = ctx({"nerve_work_status": {"ok": True, "status": {"task_id": TASK}}})
    context.has_plugin = lambda name: False
    monkeypatch.setattr(talk_nerve, "_ctx", lambda: context)
    assert talk_nerve.available() is False
    with pytest.raises(talk_nerve.NerveBridgeError, match="not loaded"):
        talk_nerve.status(TASK)


# --- rows that need a reachable remote host -----------------------------


def test_remote_rows_are_not_proven_without_a_host():
    """Delegate / cancel / reconnect / steer / approval over live SSH stay open.

    win4060 does not resolve from this host. These rows are recorded as open
    rather than passed by a stub.
    """
    pytest.skip("requires a reachable remote host (win4060): not proven here")