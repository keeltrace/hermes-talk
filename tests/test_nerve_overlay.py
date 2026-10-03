from __future__ import annotations

import json

import pytest

import talk_nerve
import talk_nerve_policy
import talk_operator_auth
import talk_tools

NERVE_TALK_TOOLS = {
    "nerve_status",
    "nerve_explain",
    "nerve_assess",
    "nerve_request_review",
}


class _Ctx:
    def __init__(self, *, loaded: bool = True):
        self.loaded = loaded
        self.calls: list[tuple[str, dict]] = []

    def has_plugin(self, plugin_id: str) -> bool:
        assert plugin_id == "nerve"
        return self.loaded

    def dispatch_tool(self, name: str, args: dict, **_kwargs) -> str:
        self.calls.append((name, args))
        return json.dumps({"ok": True, "status": {"task_id": args.get("task_id")}})


def test_nerve_voice_tools_are_operator_gated_in_shared_rooms():
    assert NERVE_TALK_TOOLS <= talk_operator_auth.MUTATING_TALK_TOOLS
    assert NERVE_TALK_TOOLS.isdisjoint(talk_operator_auth.READ_ONLY_TALK_TOOLS)


def test_default_talk_tools_advertise_nerve_only_when_available(monkeypatch):
    monkeypatch.setattr(talk_tools.talk_nerve_tools.talk_nerve, "available", lambda: False)
    names = {tool["name"] for tool in talk_tools.default_talk_tools()}
    assert NERVE_TALK_TOOLS.isdisjoint(names)

    monkeypatch.setattr(talk_tools.talk_nerve_tools.talk_nerve, "available", lambda: True)
    names = {tool["name"] for tool in talk_tools.default_talk_tools()}
    assert names >= NERVE_TALK_TOOLS


def test_status_bridge_uses_public_hermes_dispatch_and_exact_task_id(monkeypatch):
    ctx = _Ctx()
    monkeypatch.setattr(talk_nerve.talk_host, "get_ctx", lambda: ctx)
    receipt = talk_nerve.status("task-42")
    assert receipt["status"]["task_id"] == "task-42"
    assert ctx.calls == [("nerve_work_status", {"task_id": "task-42"})]

    with pytest.raises(talk_nerve.NerveBridgeError, match="task_id is required"):
        talk_nerve.status("  ")


def test_unloaded_nerve_fails_closed_before_dispatch(monkeypatch):
    ctx = _Ctx(loaded=False)
    monkeypatch.setattr(talk_nerve.talk_host, "get_ctx", lambda: ctx)
    assert talk_nerve.available() is False
    with pytest.raises(talk_nerve.NerveBridgeError, match="not loaded"):
        talk_nerve.status("task-42")
    assert ctx.calls == []


def test_speech_policy_keeps_watch_quiet_and_review_urgent():
    watch = talk_nerve_policy.classify(
        {"status": {"task_id": "task-42", "nerve": {"level": "WATCH"}}}
    )
    assert watch.speak is False
    assert watch.level == talk_nerve_policy.QUIET

    review = talk_nerve_policy.classify(
        {
            "status": {
                "task_id": "task-42",
                "nerve": {
                    "level": "ORCH_REVIEW",
                    "requires_orchestrator_review": True,
                },
            }
        }
    )
    assert review.speak is True
    assert review.interrupt is True
    assert review.level == talk_nerve_policy.URGENT


def test_review_tool_is_a_review_handoff_not_a_kill_primitive(monkeypatch):
    monkeypatch.setattr(talk_tools.talk_nerve_tools.talk_nerve, "available", lambda: True)
    schema = next(
        tool
        for tool in talk_tools.default_talk_tools()
        if tool["name"] == "nerve_request_review"
    )
    assert "NOT a stop or kill" in schema["description"]
    assert "nerve_kill" not in {tool["name"] for tool in talk_tools.default_talk_tools()}
