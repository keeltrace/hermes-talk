# Nerve integration for Hermes Talk

This overlay connects Hermes Talk to Nerve without turning either plugin into the other.

## Authority model

```text
operator voice / typed input
          |
          v
     Hermes Talk
  conversation + speech
          |
          v
       Hermes
 canonical task authority
    /             \
   v               v
Nerve             task workers
watchdog          (Hermes/Codex/
+ evidence         Nerve SSH when supported)
```

Hard invariant: **Nerve is a watchdog/forecaster, not the final lifecycle authority.** `nerve_request_review` asks canonical Hermes review. The Talk integration adds no Nerve kill verb. Nerve's existing bounded budget extension remains Nerve policy; economic pressure alone does not become a Talk stop command.

## What the overlay adds

- `talk_nerve.py` — public bridge through `PluginContext.dispatch_tool`. It does not import Nerve internals or read Nerve databases.
- `talk_nerve_tools.py` — conditional realtime tools: `nerve_status`, `nerve_explain`, `nerve_assess`, `nerve_request_review`.
- `talk_nerve_policy.py` — deterministic quiet/notify/urgent policy and dedupe gate for future push announcements.
- `talk_nerve_watch.py` — exact-task status watcher that only emits policy-admitted receipts; it never guesses a task by recency.
- `talk_nerve_provider.py` — optional generic Hermes `TaskWorkerProvider` backed by Nerve's remote-worker tools. It registers only on a host exposing the canonical worker-provider API.
- All four Nerve Talk tools are placed behind Talk's fresh-operator-speech permit in shared voice rooms. `nerve_status` and `nerve_explain` do not mutate Nerve, but their task/DoD state can be sensitive, so they are operator-gated rather than room-wide read-only tools. Status/explain require an exact canonical task ID.

## Deliberately unsupported instead of faked

The current Nerve remote API does not provide arbitrary text steering receipts or remote Hermes approval-request bridging. The Nerve SSH worker provider therefore reports steering as unsupported and exposes no fake approvals. Cancellation is supported for the exact Nerve job ID.

The current public Hermes source inspected while building this bundle did not expose `register_task_worker_provider`, even though Hermes Talk already feature-detects it. On such hosts the Nerve worker provider stays unavailable; Talk never falls back to direct SSH. The patcher also installs six integration regressions into Talk so the fork tests the Nerve tool/auth/policy bridge in its own suite.

## Configuration

When your Hermes build exposes task worker providers, add Talk plugin settings similar to:

```yaml
plugins:
  entries:
    hermes-talk:
      settings:
        nerve_worker:
          host: win4060
          profile: default
          workspace: /path/allowed/by/nerve
          max_turns: 100
          require_claim_fence: true
```

The `host` is a **Nerve-configured alias**, not a raw SSH destination. Nerve's own `remote_hosts` allowlist remains authoritative.

## Voice behavior target

Routine progress and WATCH should be silent. REPLAN should be a short notification. A review requirement, BLOCK/STOP_REQUESTED escalation, Definition-of-Done contradiction, blocked worker, or terminal failure should be urgent. Completion and an automatic bounded extension deserve one concise notice. The supplied `talk_nerve_policy.py` implements and tests those decisions, but the bundle does **not** wire ambient push speech into Talk's canonical task-event presentation path yet; see `LOCAL_HERMES_FINISH.md` in the bundle root.
