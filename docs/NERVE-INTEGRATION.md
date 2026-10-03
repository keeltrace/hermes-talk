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
- `talk_nerve_policy.py` — deterministic quiet/notify/urgent policy and dedupe gate for push announcements.
- `talk_nerve_watch.py` — exact-task status watcher that only emits policy-admitted receipts; it never guesses a task by recency.
- `talk_nerve_provider.py` — optional generic Hermes `TaskWorkerProvider` backed by Nerve's remote-worker tools. It registers only on a host exposing the canonical worker-provider API.
- `TaskEvents.observe_nerve` — the canonical projection of a Nerve verdict into Talk's task-event and speech-receipt substrate. See "Voice behavior" below.
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

## Voice behavior

Routine progress and WATCH are silent. REPLAN is a short notification. A review requirement, BLOCK/STOP_REQUESTED escalation, Definition-of-Done contradiction, blocked worker, or terminal failure is urgent. Completion and an automatic bounded extension get one concise notice. `talk_nerve_policy.py` implements and tests those decisions.

Those verdicts reach the operator through Talk's **canonical** task-event substrate, not a private speech callback. `TaskEvents.observe_nerve` takes a policy-admitted decision and reuses the same owner, lease and run fences and the same delivery/attempt receipts as every other task event:

- correlation is by the exact task id the receipt was fetched for plus the run binding — never recency;
- `quiet` is stored and projected but is never speech-eligible;
- `notify`/`urgent` become ordinary candidates, so a single-active-speech claim arbitrates them and an escalation cannot talk over live audio;
- a duplicate Nerve receipt for the same decision is idempotent;
- reconnect never replays a spent escalation.

`tests/test_nerve_projection.py` covers those races. Nerve or worker prose never reaches the speech queue: the label is plugin-owned wording bounded by the policy.

## Worker selection

The worker picker is not a hard-coded Hermes/Codex enum. Short labels the operator and model use are resolved to provider names from the host's own `linked_child_dispatch.external_workers` advertisement, so a new provider needs no change to the picker and the model is never offered a worker the host cannot prove it has. A label this build does not understand is a bad request; a label the host does not advertise is refused rather than retried as a different worker. The recipient projection recognizes any advertised Talk worker provider, so a Nerve worker is listed and steerable under the same ownership fences as a Codex one.
