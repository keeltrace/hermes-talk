"""Exact-task Nerve supervision watcher.

The watcher intentionally stops one layer short of speech. It produces
policy decisions for an exact task id and leaves delivery to Talk's canonical
session/task event substrate. This keeps polling and supervision semantics
testable without creating an unsafe parallel speech channel.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

try:
    from . import talk_nerve, talk_nerve_policy
except ImportError:  # pragma: no cover - Talk flat-module install
    import talk_nerve
    import talk_nerve_policy


class NerveStatusWatch:
    def __init__(
        self,
        task_id: str,
        callback: Callable[[talk_nerve_policy.SpeechDecision, dict], None],
        *,
        interval_s: float = 5.0,
        cooldown_s: float = 45.0,
    ) -> None:
        self.task_id = str(task_id or "").strip()
        if not self.task_id:
            raise ValueError("NerveStatusWatch requires an exact task_id")
        self.callback = callback
        self.interval_s = max(1.0, min(float(interval_s), 60.0))
        self.gate = talk_nerve_policy.SpeechGate(cooldown_s=cooldown_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> talk_nerve_policy.SpeechDecision:
        receipt = talk_nerve.status(self.task_id)
        decision = talk_nerve_policy.classify(receipt)
        if self.gate.admit(decision):
            self.callback(decision, receipt)
        return decision

    def _run(self) -> None:
        while not self._stop.is_set():
            # Supervision telemetry must never crash the voice runtime. A
            # bridge failure is not converted into a fake Nerve judgment;
            # Talk's status/doctor path can report integration health.
            with contextlib.suppress(talk_nerve.NerveBridgeError):
                self.poll_once()
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"talk-nerve-{self.task_id[:24]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout_s)))


__all__ = ["NerveStatusWatch"]
