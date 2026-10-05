# Architecture Overview

> Module map, data flow, and design decisions for sys1.

The interactive architecture diagram — callers, the `classify()` → cascade →
wires journey, the current model roster, and the routed default chain — lives
at [`sys1-architecture.html`](sys1-architecture.html) in this folder (open in
a browser for zoom, focus views, and source links). Its spec is
[`sys1-architecture.json`](sys1-architecture.json); a static desktop render is
[`sys1-architecture.png`](sys1-architecture.png).

<add-when-implemented>
- Module dependency diagram
- classify() data flow: config → task → providers → parsers → gating → JSONL
- Cascade escalation flow
- Wire-family diagram (openai / jev / gliner)
- Fail-open and never-raise invariants
</add-when-implemented>

For the project overview, see [TECHNICAL-DOCUMENTATION.md](../TECHNICAL-DOCUMENTATION.md).
