---
title: The CLI — classify, doctor, providers, tasks
status: shipped
sources:
  - src/sys1/cli.py
related:
  - interface/api.md
  - architecture/providers-and-flags.md
---

# The CLI

`cli.py` is a thin argparse front door over the same public API the service uses — no logic lives
here. `python -m sys1 <command>` (or the `sys1` console script).

## Commands

- `classify --provider X --task Y` — runs `api.classify`, prints the gate summary and exits with
  `gating.final_exit_code` semantics (0 pass / 1 warn-escalate / 2 block), the same contract the
  git hooks in consumers rely on.
- `doctor` — `api.health_report` rendered per provider: enabled/configured/available plus the human
  `availability_reason` (disabled by flag, missing key, venv not found, no listener).
- `providers` — the registry listing (tier, wire, description); the quick answer to "what can I
  point at".
- `tasks` — `tasks.list_tasks` with descriptions.

## Gotchas

- Exit codes are part of the contract: anything that changes `final_exit_code` mapping is a
  consumer-visible change, not a CLI detail.
- The CLI reads the same config stack as everything else (`load_config`), so `SYS1_*` env vars and
  the runtime state file apply here too.
