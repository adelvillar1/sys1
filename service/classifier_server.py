#!/usr/bin/env python3
"""Load-once GLiNER2.5 classifier server — the fastino-prescribed serving mode.

Run it INSIDE the gliner venv (it needs gliner2 + torch; sys1 itself stays
stdlib-only):

    /private/tmp/gliner-decide/bin/python service/classifier_server.py \
        --model fastino/GLiNER2.5-Decide --port 8414

Then point sys1 at it (config):

    [providers]
    local_server_url = "http://127.0.0.1:8414"

Every local-family classify() then reuses the one loaded model instead of
paying the model load per subprocess call (measured 2026-09-28: 9.8s load,
361ms decode — the load is the dominant local cost). `_gliner_wire` falls
back to the subprocess automatically when the server is down, and
`sys1 doctor` shows which mode will serve. Run one server per model on its
own port (e.g. `decide_1b_server_url` for the 1B variant).

Contract (mirrors the fastino gliner25-decide-playground):

    GET  /ready  -> {"ready": bool, "model": str}
    POST /infer  {"text": str,
                  "tasks": {name: {"kind": "single"|"ordinal", "labels": [...]}},
                  "model": str?}
              -> {"tasks": {name: {"labels": [...], "probabilities": {...}}},
                  "meta": {"model", "decoder": "independent", "ms": int}}

One inference at a time: CPU threads beat concurrency (same as the
playground's lock).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENGINE = None  # the loaded Classifier; tests may swap this with a stub
_LOCK = threading.Lock()  # one inference at a time


def load_engine(model_name: str) -> None:
    """Load the classifier into ENGINE (runs in a background thread at boot)."""
    global ENGINE
    from gliner2.classification import Classifier

    ENGINE = Classifier.from_pretrained(model_name, map_location="cpu")


def build_schema(schema, tasks: dict):
    """Translate the wire's task spec into a ClassificationSchema (fake-able)."""
    for name, spec in tasks.items():
        instruction = spec.get("instruction")
        if spec.get("kind") == "ordinal":
            schema.ordinal(name, spec["labels"], instruction=instruction)
        else:
            schema.single(name, spec["labels"], instruction=instruction)
    return schema


def extract(result, tasks: dict) -> dict:
    """Pull {name: {labels, probabilities}} out of a ClassificationResult."""
    out: dict = {}
    for name in tasks:
        try:
            value = result.value(name)
            probs = result.probabilities(name)
        except Exception:
            continue  # task absent from the result -> declined downstream
        labels = list(value) if isinstance(value, (list, tuple)) else [value]
        out[name] = {
            "labels": [str(v) for v in labels],
            "probabilities": {str(k): float(v) for k, v in probs.items()},
        }
    return out


def infer(text: str, tasks: dict) -> dict:
    """One prescribed-API decode for every task in `tasks`."""
    if ENGINE is None:
        raise RuntimeError("model still loading")
    from gliner2.classification import ClassificationConfig, ClassificationSchema

    schema = build_schema(ClassificationSchema(), tasks)
    result = ENGINE.classify(
        text, schema, config=ClassificationConfig(decoder="independent", on_infeasible="relax")
    )
    return extract(result, tasks)


class Handler(BaseHTTPRequestHandler):
    model_name = "unknown"  # set at serve time

    def log_message(self, *args):  # keep stdout clean for service wrappers
        pass

    def do_GET(self):
        if self.path == "/ready":
            self._json(200, {"ready": ENGINE is not None, "model": self.model_name})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/infer":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"bad request: {e}"})
            return
        want = data.get("model")
        if want and want != self.model_name:
            self._json(
                409,
                {"error": f"server serves {self.model_name!r}, requested {want!r} — run one server per model"},
            )
            return
        text = (data.get("text") or "").strip()
        tasks = data.get("tasks") or {}
        if not text or not tasks:
            self._json(400, {"error": "text and tasks are required"})
            return
        t0 = time.perf_counter()
        try:
            with _LOCK:
                tasks_out = infer(text, tasks)
        except Exception as e:
            self._json(500, {"error": f"inference failed: {e}"})
            return
        self._json(
            200,
            {
                "tasks": tasks_out,
                "meta": {
                    "model": self.model_name,
                    "decoder": "independent",
                    "ms": int((time.perf_counter() - t0) * 1000),
                },
            },
        )

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Load-once GLiNER2.5 classifier server (fastino-prescribed API)."
    )
    ap.add_argument("--model", default="fastino/GLiNER2.5-Decide")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8414)
    args = ap.parse_args()

    Handler.model_name = args.model
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[boot] bound {args.host}:{args.port}; loading {args.model} ...", flush=True)
    threading.Thread(target=load_engine, args=(args.model,), daemon=True).start()
    print("[boot] serving /ready and /infer", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
