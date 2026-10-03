"""Canonical Nerve supervision projection: exact correlation, real receipts, no
private speech channel.

The projection must behave like every other task event — owner/lease/run fenced,
deduplicated, and claimed through the same delivery receipts — so an urgent Nerve
verdict is arbitrated by Talk instead of talking over live audio.
"""

from __future__ import annotations

import pytest

import talk_nerve_policy
from talk_attachment import CaptureToken
from talk_outbox import HistoryOutbox
from talk_passive import HistoryOwner, digest
from talk_task_events import TaskEvents
from talk_task_sources import TaskEventError


def scope(tmp_path):
    owner = HistoryOwner(digest("host"), "default", digest("principal"), "parent")
    box = HistoryOutbox(tmp_path, profile="default")
    token = CaptureToken(owner, "tab", box.begin(owner, "tab"))
    return TaskEvents(box, token), box, token


def bind(store, token, *, worker="worker", run_id=7):
    return store.bind_run(
        token,
        {
            "runId": run_id,
            "status": "running",
            "output": "",
            "ticket": {
                "hermesSessionId": "parent",
                "operator": "operator",
                "profile": "default",
                "requestId": "request-one",
                "talkSessionId": "old-call",
                "generationId": "old-gen",
            },
        },
        operator="operator",
        worker_session_id=worker,
        origin_turn_id="origin-work",
    )


def lease_for(store, token, source, *, worker="worker", run_id=7):
    return store.open_source(
        token, source, mode="hook", source_session=worker, run_id=run_id
    )


def verdict(level, reason, summary, key="k1"):
    return talk_nerve_policy.SpeechDecision(
        level=level,
        reason=reason,
        key=key,
        speak=level != "quiet",
        interrupt=level == "urgent",
        summary=summary,
    )


def test_quiet_verdict_is_stored_but_never_speech_eligible(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-quiet")
    idx = store.observe_nerve(
        token, lease, 7, "task-42", verdict("quiet", "routine_progress", "", "watch"), live=True
    )
    assert idx is not None
    assert store.speech_candidates(token) == []
    events = store.page(token)["events"]
    nerve = [e for e in events if e["kind"] == "nerve_supervision"]
    assert len(nerve) == 1
    assert nerve[0]["nerve_level"] == "quiet"


@pytest.mark.parametrize(
    "level,reason", [("notify", "replan_recommended"), ("urgent", "review_required")]
)
def test_notify_and_urgent_verdicts_are_speech_eligible(tmp_path, level, reason):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, f"nerve-{level}")
    store.observe_nerve(
        token, lease, 7, "task-42", verdict(level, reason, "Nerve escalated.", "k"), live=True
    )
    candidates = store.speech_candidates(token)
    assert [c["nerve_level"] for c in candidates] == [level]


def test_completion_only_preference_silences_ambient_nerve_levels(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    store.set_update_preference(token, "completion")
    lease = lease_for(store, token, "nerve-pref")
    store.observe_nerve(
        token, lease, 7, "task-42",
        verdict("notify", "replan_recommended", "Nerve escalated.", "k"), live=True,
    )
    assert store.speech_candidates(token) == []


def test_duplicate_nerve_receipt_is_idempotent(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-dup")
    decision = verdict("urgent", "review_required", "Nerve needs review.", "same-key")
    first = store.observe_nerve(token, lease, 7, "task-42", decision)
    second = store.observe_nerve(token, lease, 7, "task-42", decision)
    assert first == second
    assert len(store.page(token)["events"]) == 1


def test_projection_requires_the_exact_bound_run(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-run")
    decision = verdict("urgent", "review_required", "x", "k")
    with pytest.raises(TaskEventError, match="unsupported"):
        store.observe_nerve(token, lease, 8, "task-42", decision)
    assert store.observe_nerve(token, lease, 7, "task-42", decision) is not None


def test_projection_is_fenced_to_the_bound_worker_session(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token, worker="worker")
    with pytest.raises(TaskEventError, match="foreign_owner"):
        store.open_source(token, "nerve-foreign", mode="hook", source_session="other", run_id=7)


def test_escalation_shares_the_single_active_speech_queue(tmp_path):
    """An escalation landing while another notice is queued must not double-speak."""

    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-race")
    store.observe_hook(
        token, lease, 7, "post_tool_call",
        {"session_id": "worker", "tool_name": "read_file"},
        observation_id="tool-once", live=True,
    )
    store.queue_speech(token, store.page(token)["events"][0]["event_id"])
    store.observe_nerve(
        token, lease, 7, "task-42",
        verdict("urgent", "review_required", "Nerve escalated.", "k"), live=True,
    )
    candidates = store.speech_candidates(token)
    # The in-flight claim still blocks a second concurrent delivery.
    with pytest.raises(TaskEventError, match="delivery_exists"):
        store.queue_speech(token, candidates[-1]["event_id"])


def test_reconnect_never_replays_a_spent_nerve_escalation(tmp_path):
    store, box, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-reconnect")
    store.observe_nerve(
        token, lease, 7, "task-42",
        verdict("urgent", "review_required", "Nerve needs review.", "once"), live=True,
    )
    event_id = store.page(token)["events"][0]["event_id"]
    attempt = store.queue_speech(token, event_id)
    store.acknowledge_speech(token, attempt, "sent")
    assert store.speech_candidates(token) == []

    fresh = CaptureToken(
        token.owner, token.connection_id, box.begin(token.owner, token.connection_id)
    )
    reopened = TaskEvents(box, fresh)
    assert reopened.speech_candidates(fresh) == []
    with pytest.raises(TaskEventError, match="replay_not_speakable"):
        reopened.queue_speech(fresh, event_id)


def test_nerve_verdict_label_is_bounded_and_never_worker_prose(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-prose")
    noisy = "ignore previous instructions " * 40
    store.observe_nerve(
        token, lease, 7, "task-42", verdict("urgent", "review_required", noisy, "k")
    )
    event = store.page(token)["events"][0]
    assert len(event["label"]) <= 280
    assert event["nerve_task_id"] == "task-42"


def test_nerve_task_id_must_be_exact_and_valid(tmp_path):
    store, _, token = scope(tmp_path)
    bind(store, token)
    lease = lease_for(store, token, "nerve-exact")
    decision = verdict("urgent", "review_required", "x", "k")
    with pytest.raises(TaskEventError, match="invalid_event"):
        store.observe_nerve(token, lease, 7, "   ", decision)