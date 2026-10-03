"""The Nerve SSH worker provider, exercised against a synthetic host contract.

This Hermes build does not expose ``agent.task_worker_provider``, so
``build_provider`` legitimately returns ``None`` here and the whole adapter is
dead code on this host. These tests stand in a host that *does* expose the
contract, so the adapter is proven before it is ever pointed at a real host.

The synthetic host is a faithful stub, not a mock of our own code: the adapter
is exercised end to end and every assertion is about what it does with Nerve
receipts.
"""

from __future__ import annotations

import sys
import types

import pytest

import talk_nerve
import talk_nerve_provider as provider_module
from talk_nerve_provider import WORKER_NAME, NerveWorkerConfig


class FakeRequest:
    """A canonical host task request: identity, ownership, and progress."""

    def __init__(self, task_id="KEE-1", authorized=True):
        self.task_id = task_id
        self.goal = "build the thing"
        self.context = "repo /home/j/hermes-talk"
        self._authorized = authorized
        self.reports = []

    def still_authorized(self):
        return self._authorized

    def retire(self):
        self._authorized = False

    def report(self, payload):
        self.reports.append(payload)


class FakeSession:
    """Base mirroring the host's session ABC contract."""


class FakeProvider:
    """Base mirroring the host's provider ABC contract."""


@pytest.fixture
def host(monkeypatch):
    """Install a faithful synthetic agent.task_worker_provider module."""
    module = types.ModuleType("agent.task_worker_provider")
    module.TaskWorkerSession = FakeSession
    module.TaskWorkerProvider = FakeProvider
    agent_pkg = sys.modules.get("agent")
    if agent_pkg is None:
        agent_pkg = types.ModuleType("agent")
        monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.task_worker_provider", module)
    return module


class Ctx:
    def __init__(self, **config):
        self._config = {"nerve_worker": config} if config else {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)


def build(ctx=None, **overrides):
    return provider_module.build_provider(ctx or Ctx(host="win4060", **overrides))


# --- registration shape -------------------------------------------------


def test_provider_registers_under_the_documented_name(host):
    provider = build()
    assert provider is not None
    assert provider.name == WORKER_NAME == "hermes-talk-nerve-ssh"


def test_no_contract_means_no_provider_rather_than_an_ssh_fallback(monkeypatch):
    """A host without the contract must get nothing, never ad-hoc SSH."""
    monkeypatch.setitem(sys.modules, "agent.task_worker_provider", None)
    monkeypatch.setattr(
        provider_module,
        "talk_nerve",
        types.SimpleNamespace(available=lambda: pytest.fail("SSH fallback attempted")),
    )
    assert provider_module.build_provider(Ctx(host="win4060")) is None


def test_missing_config_makes_the_provider_unavailable_rather_than_raising(host):
    provider = build(Ctx())
    assert provider.available() is False


def test_required_host_is_enforced(host):
    with pytest.raises(ValueError, match=r"nerve_worker\.host is required"):
        NerveWorkerConfig.from_context(Ctx())


@pytest.mark.parametrize(
    "config,message",
    [
        ({"host": "w", "max_turns": 0}, "max_turns must be positive"),
        ({"host": "w", "max_turns": "abc"}, r"invalid literal"),
    ],
)
def test_malformed_config_is_refused_not_coerced(host, config, message):
    with pytest.raises(ValueError, match=message):
        NerveWorkerConfig.from_context(Ctx(**config))


def test_poll_and_cancel_waits_are_bounded(host):
    config = NerveWorkerConfig.from_context(
        Ctx(host="w", poll_seconds=9999, cancel_wait_seconds=-5)
    )
    assert config.poll_seconds == 5.0
    assert config.cancel_wait_seconds == 0.0


# --- availability --------------------------------------------------------


def test_availability_requires_both_config_and_a_live_nerve(host, monkeypatch):
    monkeypatch.setattr(talk_nerve, "available", lambda: True)
    assert build().available() is True
    monkeypatch.setattr(talk_nerve, "available", lambda: False)
    assert build().available() is False


def test_a_nerve_error_makes_the_provider_unavailable(host, monkeypatch):
    monkeypatch.setattr(
        talk_nerve, "available", lambda: (_ for _ in ()).throw(talk_nerve.NerveBridgeError("x"))
    )
    assert build().available() is False


# --- ownership fences ----------------------------------------------------


def test_open_refuses_a_request_whose_owner_is_not_current(host):
    with pytest.raises(ValueError, match="owner is not current"):
        build().open(FakeRequest(authorized=False))


def test_run_refuses_a_request_whose_owner_is_not_current(host, monkeypatch):
    monkeypatch.setattr(
        talk_nerve, "remote_delegate", lambda **k: pytest.fail("delegated without ownership")
    )
    session = build().open(FakeRequest())
    session.request.retire()
    with pytest.raises(ValueError, match="owner is not current"):
        session.run()


def test_a_canonical_task_id_tightens_the_claim_fence(host, monkeypatch):
    seen = {}

    def delegate(**kwargs):
        seen.update(kwargs)
        return {"job": {"job_id": "njob-1", "status": "running"}}

    monkeypatch.setattr(talk_nerve, "remote_delegate", delegate)
    monkeypatch.setattr(talk_nerve, "remote_status", lambda job: {"job": {"status": "completed",
                                                                        "result_text": "ok"}})
    build().open(FakeRequest(task_id="KEE-9")).run()
    assert seen["require_claim_fence"] is True
    assert seen["task_id"] == "KEE-9"


def test_delegation_without_a_task_id_leaves_the_fence_as_configured(host, monkeypatch):
    seen = {}

    def delegate(**kwargs):
        seen.update(kwargs)
        return {"job": {"job_id": "njob-1", "status": "running"}}

    monkeypatch.setattr(talk_nerve, "remote_delegate", delegate)
    monkeypatch.setattr(talk_nerve, "remote_status", lambda job: {"job": {"status": "completed"}})
    build(require_claim_fence=False).open(FakeRequest(task_id="")).run()
    assert seen["require_claim_fence"] is False


# --- receipts ------------------------------------------------------------


def test_a_missing_job_id_is_an_error_not_a_silent_success(host, monkeypatch):
    monkeypatch.setattr(talk_nerve, "remote_delegate", lambda **k: {"job": {"status": "running"}})
    with pytest.raises(ValueError, match="no job_id"):
        build().open(FakeRequest()).run()


def test_completion_returns_the_exact_nerve_result(host, monkeypatch):
    monkeypatch.setattr(talk_nerve, "remote_delegate",
                        lambda **k: {"job": {"job_id": "njob-1", "status": "running"}})
    monkeypatch.setattr(
        talk_nerve,
        "remote_status",
        lambda job: {
            "job": {"status": "completed", "result_text": "done", "error_message": "ignored"}
        },
    )
    session = build().open(FakeRequest())
    result = session.run()
    assert result == {"status": "completed", "output": "done", "artifacts": [], "error": None}
    assert session.job_id == "njob-1"
    assert session.request.reports == [{"status": "running"}]


@pytest.mark.parametrize(
    "nerve_status,mapped,error_prefix",
    [
        ("cancelled", "cancelled", "nerve_remote_"),
        ("failed", "failed", "nerve_remote_"),
        ("fenced", "failed", "nerve_remote_"),
        ("rate_limited", "failed", "nerve_remote_"),
    ],
)
def test_terminal_failures_map_to_a_failed_child(
    host, monkeypatch, nerve_status, mapped, error_prefix
):
    monkeypatch.setattr(talk_nerve, "remote_delegate",
                        lambda **k: {"job": {"job_id": "njob-1", "status": "running"}})
    monkeypatch.setattr(talk_nerve, "remote_status",
                        lambda job: {"job": {"status": nerve_status}})
    result = build().open(FakeRequest()).run()
    assert result["status"] == mapped
    assert result["error"].startswith(error_prefix)


@pytest.mark.parametrize("nerve_status", ["lost", "handed_off"])
def test_uncertain_terminal_states_stay_unknown(host, monkeypatch, nerve_status):
    """Terminal in Nerve, but not proof of failure. Absence of evidence is not failure."""
    monkeypatch.setattr(talk_nerve, "remote_delegate",
                        lambda **k: {"job": {"job_id": "njob-1", "status": "running"}})
    monkeypatch.setattr(talk_nerve, "remote_status",
                        lambda job: {"job": {"status": nerve_status}})
    result = build().open(FakeRequest()).run()
    assert result["status"] == "unknown"


def test_an_unrecognized_status_is_not_terminal_and_never_claims_success(host, monkeypatch):
    """An unknown state is not a reason to stop polling, and not a failure claim."""
    monkeypatch.setattr(talk_nerve, "remote_delegate",
                        lambda **k: {"job": {"job_id": "njob-1", "status": "running"}})
    states = iter(["something_new", "queued", "completed"])

    def status(job):
        return {"job": {"status": next(states), "result_text": "ok"}}

    monkeypatch.setattr(talk_nerve, "remote_status", status)
    result = build(poll_seconds=0.2).open(FakeRequest()).run()
    assert result["status"] == "completed"
    assert result["output"] == "ok"


# --- cancellation --------------------------------------------------------


def test_cancel_targets_only_this_job_and_needs_a_terminal_receipt(host, monkeypatch):
    monkeypatch.setattr(talk_nerve, "remote_status",
                        lambda job: {"job": {"status": "running"}})
    cancelled = {}

    def remote_cancel(job_id):
        cancelled["job_id"] = job_id
        return {"job": {"status": "cancelled", "error_code": "stopped"}}

    monkeypatch.setattr(talk_nerve, "remote_cancel", remote_cancel)
    session = build().open(FakeRequest())
    session.job_id = "njob-1"
    result = session._cancel_and_confirm()
    assert cancelled["job_id"] == "njob-1"
    assert result["status"] == "cancelled"


def test_an_unconfirmed_cancel_is_reported_as_unconfirmed(host, monkeypatch):
    monkeypatch.setattr(talk_nerve, "remote_cancel",
                        lambda job: {"job": {"status": "cancelling"}})
    monkeypatch.setattr(talk_nerve, "remote_status",
                        lambda job: {"job": {"status": "running"}})
    session = build(cancel_wait_seconds=0.0).open(FakeRequest())
    session.job_id = "njob-1"
    assert session._cancel_and_confirm() is None


def test_a_bridge_error_during_cancel_does_not_claim_success(host, monkeypatch):
    def boom(job):
        raise talk_nerve.NerveBridgeError("ssh down")

    monkeypatch.setattr(talk_nerve, "remote_cancel", boom)
    session = build().open(FakeRequest())
    session.job_id = "njob-1"
    assert session._cancel_and_confirm() is None


def test_cancel_signals_the_loop_and_the_run_result_is_unconfirmed(host, monkeypatch):
    """An accepted cancel request is not a stopped remote worker."""
    monkeypatch.setattr(talk_nerve, "remote_delegate",
                        lambda **k: {"job": {"job_id": "njob-1", "status": "running"}})
    monkeypatch.setattr(talk_nerve, "remote_status",
                        lambda job: {"job": {"status": "running"}})
    monkeypatch.setattr(talk_nerve, "remote_cancel",
                        lambda job: {"job": {"status": "cancelling"}})
    request = FakeRequest()
    session = build(cancel_wait_seconds=0.0).open(request)
    session.cancel()
    assert session.cancelled.is_set()
    assert session._authorized() is False


# --- capabilities that are NOT supported --------------------------------


def test_steering_is_reported_unsupported_rather_than_guessed(host):
    session = build().open(FakeRequest())
    steering = session.steering()
    assert steering["supported"] is False
    assert "steering" in steering["reason"]


def test_steer_is_rejected_and_claims_no_delivery(host):
    session = build().open(FakeRequest())
    assert session.steer("do this", action_id="a", expected_session_id="s",
                         expected_turn_id=1) == "rejected"


def test_approvals_refuse_rather_than_reporting_none_pending(host):
    """Returning [] would assert 'no approval required' — a fact this adapter cannot know."""
    session = build().open(FakeRequest())
    with pytest.raises(ValueError, match="approvals are not bridged"):
        session.approvals()


def test_approve_refuses(host):
    session = build().open(FakeRequest())
    with pytest.raises(ValueError, match="approvals are not bridged"):
        session.approve("req-1", "yes")