---
title: Telemetry and calibration — the JSONL contract and the feedback join
status: shipped
sources:
  - src/sys1/telemetry.py
  - src/sys1/api.py
  - tests/test_telemetry.py
related:
  - foundation/charter.md
  - interface/service.md
---

# Telemetry and calibration

`telemetry.py` writes the JSONL stream and the calibration helpers on top of it. Same on-disk shape
as dev-decisions: `<log_dir>/YYYY/MM/DD/events.jsonl`, so existing dashboards can point at either
location.

## The record contract

`api._log_classify` builds the classify record: `op`, `task`, `provider`, `providers_used`,
`input_chars`, `input_sha256` (16-hex prefix of the sha256 of `text`; joins feedback rows),
`answers`, `verdict`, `escalated`, `latency_ms`, `telemetry` (per-provider
`http_status`/`latency_ms`/`retries`/`structured_ok`/token counts and provider extras),
`confidence_histogram`. Cascade calls add `cascade`, `chain`, `winner`, `route_chain`,
`route_reason`; calls with images add `image_count` (the count only — the base64 payload never
enters the record). **Adding keys is fine; renaming or removing is a breaking change** (the
dashboard and calibration tooling consume these).

`log_record` must never raise: it deep-copies the record, stamps `ts`, redacts the configured
fields (`log.redact_fields`: content, context, match, `_raw`) via `_redact_field`, and swallows
every error (returns `None`). A broken disk must not break a git hook. `sanitize_for_log` applies
the same redaction to the answers subtree before it enters the record.

## Calibration helpers

- `confidence_histogram` — per-provider confidence bucketed into 10 bins for the dashboard.
- `provider_agreement` — cross-provider agreement per head (agree/disagree/majority/distribution),
  ported from zcode-router-kit's reconciliation ledger.
- `record_feedback` — one feedback row (`op: "feedback"`) joining `events.jsonl` via
  `input_sha256`: predicted label + confidence + provider vs the human's `actual_label`.
- `calibration_curve` — per-bin predicted-vs-actual accuracy from a feedback stream; the basis for
  fitting real floors (published thresholds do not transfer across workloads).

## Gotchas

- Redaction only rewrites *string* values; a non-string in a redacted field passes through.
- `consistency_classify` logs n ordinary records (one per draw), not a special record — no new
  contract; join by `input_sha256`.
