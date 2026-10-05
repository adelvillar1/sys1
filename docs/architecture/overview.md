# Architecture Overview

> Module map, data flow, and design decisions for sys1.

The interactive architecture diagram — callers, the `classify()` → cascade →
wires journey, the current model roster, and the routed default chain — lives
at [`.archify/architecture-sys1-20261005-152027/sys1-architecture.html`](../../.archify/architecture-sys1-20261005-152027/sys1-architecture.html)
(open in a browser).

<add-when-implemented>
- Module dependency diagram
- classify() data flow: config → task → providers → parsers → gating → JSONL
- Cascade escalation flow
- Wire-family diagram (openai / jev / gliner)
- Fail-open and never-raise invariants
</add-when-implemented>

For the project overview, see [TECHNICAL-DOCUMENTATION.md](../TECHNICAL-DOCUMENTATION.md).
