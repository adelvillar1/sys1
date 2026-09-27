"""
sys1 control dashboard.

A single-page operator console for the sys1 service: provider flags (enable /
disable candidate models without touching config files), endpoint probes, a
live classify console, a feedback capture loop for calibration, and the recent
JSONL activity stream.

Mounted by `service/main.py` (`app.include_router(router)`). Run the whole
thing with:

    uvicorn service.main:app --host 127.0.0.1 --port 8400
    open http://127.0.0.1:8400/dashboard

Endpoints added here:

    GET  /dashboard                     this page (HTML)
    GET  /v1/providers                  full registry + flag/config/health view
    POST /v1/providers/{pid}/flag       {"enabled": bool} → persists state.json
    POST /v1/providers/{pid}/probe      TCP/venv reachability probe
    GET  /v1/recent?limit=15            tail of the JSONL event log

Auth mirrors the rest of the service: when SYS1_BEARER_TOKEN is set, the
mutating and data endpoints require the bearer header; the dashboard page
itself is always served (the JS prompts for a token and keeps it in
sessionStorage).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import sys1

router = APIRouter()


class FlagRequest(BaseModel):
    enabled: bool = Field(False, description="turn this provider's flag on/off")

# ── auth (shared with main.py's dependency when mounted) ─────────────────────


def require_auth(request: Request) -> None:
    """Same contract as service.main.require_auth, re-declared to keep this
    module importable standalone (main.py loads it by path)."""
    expected = _bearer_expected()
    if not expected:
        return
    authorization = request.headers.get("authorization") or ""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    import hmac

    if not hmac.compare_digest(authorization.split(" ", 1)[1].strip(), expected):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def _bearer_expected() -> str | None:
    import os

    return os.environ.get("SYS1_BEARER_TOKEN")


# ── flag mutation ─────────────────────────────────────────────────────────────


def _split(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    return [p.strip().lower() for p in re.split(r"[+,;\s]+", str(raw or "")) if p.strip()]


def _set_flag(pid: str, enable: bool) -> dict:
    """Rewrite the persisted flag state so `pid` ends up enabled/disabled."""
    if pid not in sys1.REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")

    cfg = sys1.load_config()
    pcfg = cfg.get("providers", {})
    enabled_tokens = _split(pcfg.get("enabled", "core")) or ["core"]
    disabled_ids = _split(pcfg.get("disabled", ""))
    effective = sys1.enabled_set(cfg)

    if enable:
        disabled_ids = [d for d in disabled_ids if d != pid]
        if pid not in effective:
            enabled_tokens = [*enabled_tokens, pid]
    else:
        if pid in effective:
            disabled_ids = list(dict.fromkeys([*disabled_ids, pid]))
        enabled_tokens = [t for t in enabled_tokens if t != pid]

    sys1.write_state_flags(enabled_tokens, disabled_ids)
    return provider_payload(sys1.load_config())


def provider_payload(cfg: dict | None = None) -> dict:
    report = sys1.provider_report(cfg)
    return {
        "version": sys1.__version__,
        "auth": "bearer" if _bearer_expected() else "open",
        "enabled": sorted(sys1.enabled_set(cfg or sys1.load_config())),
        "state_file": str(sys1.state_file_path()),
        "providers": report,
    }


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("/v1/providers")
def providers_endpoint() -> JSONResponse:
    # open like /v1/health: exposes only non-secret config shape (URLs, model
    # names, whether a key env var is set — never the key value).
    return JSONResponse(provider_payload())


@router.post("/v1/providers/{pid}/flag", dependencies=[Depends(require_auth)])
def providers_flag(pid: str, body: FlagRequest) -> JSONResponse:
    return JSONResponse(_set_flag(pid.lower(), body.enabled))


@router.post("/v1/providers/{pid}/probe", dependencies=[Depends(require_auth)])
def providers_probe(pid: str) -> JSONResponse:
    if pid.lower() not in sys1.REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown provider {pid!r}")
    return JSONResponse(sys1.probe(pid.lower()))


# ── recent activity (JSONL tail) ──────────────────────────────────────────────


def _log_root(cfg: dict) -> Path:
    return Path(cfg["classify"].get("log_dir") or (Path.home() / ".local" / "share" / "sys1" / "logs")).expanduser()


def _labels_summary(row: dict) -> str:
    parts: list[str] = []
    for _prov, heads in (row.get("answers") or {}).items():
        for head_id, entry in (heads or {}).items():
            if head_id.startswith("_") or not isinstance(entry, dict):
                continue
            label = entry.get("label") or entry.get("score") or entry.get("noul")
            if label is None:
                continue
            parts.append(f"{head_id}={label}")
    return "; ".join(parts[:8])


@router.get("/v1/recent", dependencies=[Depends(require_auth)])
def recent_endpoint(limit: int = Query(15, ge=1, le=200)) -> JSONResponse:
    cfg = sys1.load_config()
    root = _log_root(cfg)
    rows: list[dict] = []
    if root.exists():
        files = sorted(root.rglob("events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines[-200:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(
                    {
                        "ts": rec.get("ts", ""),
                        "op": rec.get("op", ""),
                        "task": rec.get("task", ""),
                        "provider": rec.get("provider", ""),
                        "providers_used": rec.get("providers_used", []),
                        "verdict": rec.get("verdict", ""),
                        "escalated": rec.get("escalated", False),
                        "latency_ms": rec.get("latency_ms"),
                        "input_sha256": rec.get("input_sha256", ""),
                        "labels": _labels_summary(rec),
                        "error": (rec.get("reason") or ""),
                    }
                )
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
    return JSONResponse({"generated_at": datetime.now(timezone.utc).isoformat(), "rows": rows})


# ── the page ──────────────────────────────────────────────────────────────────

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sys1 · control</title>
<style>
  :root {
    --bg: #0b0e14; --panel: #11151f; --panel2: #161b28; --line: #222a3a;
    --text: #d7dde8; --dim: #8b94a7; --faint: #5b647a;
    --green: #3fb27f; --amber: #d9a441; --red: #e5615c; --grey: #4d5567;
    --accent: #6ea8fe; --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { display: flex; align-items: baseline; gap: 14px; padding: 18px 24px;
           border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  header h1 { font: 600 18px var(--mono); margin: 0; letter-spacing: .5px; }
  header .ver { color: var(--faint); font: 12px var(--mono); }
  header .sp { flex: 1; }
  header input { background: var(--panel2); border: 1px solid var(--line); color: var(--text);
                 border-radius: 6px; padding: 6px 10px; font: 12px var(--mono); width: 200px; }
  main { max-width: 1280px; margin: 0 auto; padding: 20px 24px 60px; }
  h2 { font: 600 13px var(--mono); text-transform: uppercase; letter-spacing: 1.2px;
       color: var(--dim); margin: 30px 0 12px; }
  h2 .n { color: var(--faint); font-weight: 400; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
          padding: 14px 16px; position: relative; }
  .card.candidate { border-style: dashed; }
  .row { display: flex; align-items: center; gap: 8px; }
  .name { font: 600 14px var(--mono); }
  .chips { display: flex; gap: 6px; margin-left: auto; }
  .chip { font: 10px var(--mono); color: var(--dim); border: 1px solid var(--line);
          border-radius: 4px; padding: 1px 6px; text-transform: uppercase; letter-spacing: .5px; }
  .pill { display: inline-flex; align-items: center; gap: 6px; font: 11px var(--mono);
          border-radius: 999px; padding: 3px 10px; margin: 8px 0 4px; }
  .pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  .pill.ready { color: var(--green); background: rgba(63,178,127,.10); }
  .pill.unconf { color: var(--amber); background: rgba(217,164,65,.10); }
  .pill.down { color: var(--red); background: rgba(229,97,92,.10); }
  .pill.off { color: var(--grey); background: rgba(77,85,103,.12); }
  .reason { color: var(--dim); font: 12px var(--mono); min-height: 16px; }
  .kv { color: var(--faint); font: 11px var(--mono); margin-top: 6px; word-break: break-all; }
  .kv b { color: var(--dim); font-weight: 500; }
  .setup { color: var(--amber); font: 11px/1.4 var(--mono); margin-top: 8px; display: none; }
  .setup.show { display: block; }
  .actions { margin-top: 10px; display: flex; gap: 10px; align-items: center; }
  button { background: var(--panel2); border: 1px solid var(--line); color: var(--text);
           font: 12px var(--mono); border-radius: 6px; padding: 5px 12px; cursor: pointer; }
  button:hover { border-color: var(--accent); }
  button:disabled { opacity: .5; cursor: default; }
  .switch { margin-left: auto; display: inline-flex; align-items: center; gap: 8px;
            font: 11px var(--mono); color: var(--dim); cursor: pointer; }
  .track { width: 34px; height: 18px; border-radius: 999px; background: var(--line);
           position: relative; transition: background .15s; }
  .track::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
                  border-radius: 50%; background: var(--faint); transition: left .15s, background .15s; }
  .switch.on .track { background: rgba(63,178,127,.45); }
  .switch.on .track::after { left: 18px; background: var(--green); }
  .probe { color: var(--dim); font: 11px var(--mono); }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
  select, textarea { background: var(--panel2); color: var(--text); border: 1px solid var(--line);
                     border-radius: 6px; font: 12px var(--mono); padding: 7px 10px; }
  select { min-width: 150px; }
  textarea { width: 100%; min-height: 130px; resize: vertical; margin-top: 10px; }
  .runbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; }
  .runbar label { font: 11px var(--mono); color: var(--dim); }
  .runbar input[type=number] { width: 70px; background: var(--panel2); color: var(--text);
                               border: 1px solid var(--line); border-radius: 6px; padding: 6px; }
  .pv { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
  .pv button { border-radius: 999px; opacity: .45; }
  .pv button.sel { opacity: 1; border-color: var(--accent); color: var(--accent); }
  table { border-collapse: collapse; width: 100%; font: 12px var(--mono); }
  th { text-align: left; color: var(--faint); font-weight: 500; padding: 6px 10px;
       border-bottom: 1px solid var(--line); }
  td { padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .bar { display: inline-block; width: 90px; height: 6px; border-radius: 3px; background: var(--line);
         margin-right: 8px; vertical-align: middle; position: relative; overflow: hidden; }
  .bar > i { position: absolute; inset: 0; background: var(--accent); border-radius: 3px; }
  .verdict { display: inline-block; font: 600 12px var(--mono); border-radius: 6px; padding: 4px 14px;
             margin: 12px 0; text-transform: uppercase; letter-spacing: 1px; }
  .v-pass { color: var(--green); background: rgba(63,178,127,.12); }
  .v-escalate { color: var(--amber); background: rgba(217,164,65,.12); }
  .v-block { color: #fff; background: var(--red); }
  .v-error { color: var(--red); background: rgba(229,97,92,.12); border: 1px dashed var(--red); }
  .tele { color: var(--faint); font-size: 11px; }
  .exp { display: flex; gap: 6px; align-items: center; }
  .exp input { width: 90px; padding: 3px 6px; font: 11px var(--mono); background: var(--panel2);
               border: 1px solid var(--line); border-radius: 4px; color: var(--text); }
  .exp button { padding: 3px 8px; }
  .ok { color: var(--green); } .err { color: var(--red); }
  footer { color: var(--faint); font: 11px var(--mono); text-align: center; padding: 30px 0 10px; }
  a { color: var(--accent); }
</style>
</head>
<body>
<header>
  <h1>sys1 <span style="color:var(--accent)">·</span> control</h1>
  <span class="ver" id="meta">loading…</span>
  <span class="sp"></span>
  <input id="token" type="password" placeholder="bearer token (if the service uses one)" autocomplete="off">
</header>
<main>
  <h2>Providers <span class="n" id="pcount"></span></h2>
  <div class="grid" id="providers"></div>

  <h2>Live classify</h2>
  <div class="panel">
    <div class="runbar">
      <select id="task"></select>
      <label>floor <input id="floor" type="number" min="0" max="1" step="0.05" value="0.7"></label>
      <label><input id="logit" type="checkbox" checked style="accent-color:var(--accent)"> log to calibration</label>
      <span class="sp"></span>
      <button id="run" class="ok" style="border-color:var(--green)">▶ run</button>
    </div>
    <div class="pv" id="pvsel"></div>
    <textarea id="text" placeholder="paste a diff, PR body, command, issue…"></textarea>
    <div id="result"></div>
  </div>

  <h2>Recent activity <button id="refresh" style="float:right;margin-top:-4px">↻</button></h2>
  <div class="panel" style="padding:4px 10px"><table id="recent"><tr><td>loading…</td></tr></table></div>

  <footer>sys1 <span id="fver"></span> · <a href="/docs">API docs</a> · flags persist to <span id="spath">state.json</span></footer>
</main>
<script>
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

$("#token").value = sessionStorage.getItem("sys1_token") || "";
$("#token").addEventListener("change", () => { sessionStorage.setItem("sys1_token", $("#token").value); refreshAll(); });

async function api(path, opts = {}) {
  opts.headers = opts.headers || {};
  opts.headers["Content-Type"] = "application/json";
  const tok = $("#token").value.trim();
  if (tok) opts.headers["Authorization"] = "Bearer " + tok;
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.status + " " + r.statusText;
    try { const j = await r.json(); if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── providers ────────────────────────────────────────────────────────────────

function pillFor(p) {
  if (!p.enabled) return '<span class="pill off">disabled by flag</span>';
  if (p.available) return '<span class="pill ready">ready</span>';
  if (!p.configured) return '<span class="pill unconf">not configured</span>';
  return '<span class="pill down">unreachable</span>';
}

function cfgLine(c) {
  if (c.kind === "venv") {
    return `<b>venv</b> ${esc(c.venv)} · <b>model</b> ${esc(c.model)}`;
  }
  let l = `<b>url</b> ${esc(c.url)} · <b>model</b> ${esc(c.model)}`;
  if (c.api_key_env) l += ` · <b>${esc(c.api_key_env)}</b> ${c.api_key_set ? "set" : "not set"}`;
  return l;
}

let lastProviders = null;
async function renderProviders() {
  const data = await api("/v1/providers");
  lastProviders = data;
  $("#meta").textContent = `v${data.version} · ${data.auth === "bearer" ? "bearer auth" : "open"} · ${new Date(data.enabled.length ? Date.now() : Date.now()).toLocaleTimeString()}`;
  $("#spath").textContent = data.state_file;
  $("#fver").textContent = "v" + data.version;
  const ps = Object.values(data.providers).sort((a, b) => (a.tier === b.tier ? a.id.localeCompare(b.id) : a.tier === "core" ? -1 : 1));
  $("#pcount").textContent = `${data.enabled.length} enabled / ${ps.length} registered`;
  $("#providers").innerHTML = ps.map((p) => `
    <div class="card ${p.tier}" id="card-${p.id}">
      <div class="row">
        <span class="name">${esc(p.name)}</span>
        <span class="chips">
          <span class="chip">${esc(p.tier)}</span>
          <span class="chip">${esc(p.wire)}</span>
        </span>
      </div>
      ${pillFor(p)}
      <div class="reason">${esc(p.reason)}</div>
      <div style="color:var(--dim);font-size:12px">${esc(p.description)}</div>
      <div class="kv">${cfgLine(p.config)}</div>
      ${p.meta && p.meta.setup ? `<div class="setup" id="su-${p.id}"><b>setup</b> ${esc(p.meta.setup)}</div>` : ""}
      <div class="actions">
        <button data-probe="${p.id}">probe</button>
        <span class="probe" id="pr-${p.id}"></span>
        <span class="switch ${p.enabled ? "on" : ""}" id="sw-${p.id}" data-toggle="${p.id}">
          <span class="track"></span>${p.enabled ? "on" : "off"}
        </span>
      </div>
    </div>`).join("");

  document.querySelectorAll("[data-probe]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await api(`/v1/providers/${b.dataset.probe}/probe`, { method: "POST" });
      const el = $("#pr-" + b.dataset.probe);
      el.innerHTML = r.ok
        ? `<span class="ok">✓ ${esc(r.detail)}${r.latency_ms != null ? " · " + r.latency_ms + "ms" : ""}</span>`
        : `<span class="err">✗ ${esc(r.detail)}</span>`;
    } catch (e) { $("#pr-" + b.dataset.probe).textContent = e.message; }
    b.disabled = false;
  }));
  document.querySelectorAll("[data-toggle]").forEach((sw) => sw.addEventListener("click", async () => {
    const id = sw.dataset.toggle;
    const enable = !sw.classList.contains("on");
    try {
      const data = await api(`/v1/providers/${id}/flag`, { method: "POST", body: JSON.stringify({ enabled: enable }) });
      applyProviders(data);
      renderPvButtons(data);
    } catch (e) { alert("flag failed: " + e.message); }
  }));
  // clicking a candidate card name reveals its setup hint
  ps.forEach((p) => {
    const su = $("#su-" + p.id);
    if (su) {
      const card = $("#card-" + p.id);
      card.addEventListener("mouseenter", () => su.classList.add("show"));
      card.addEventListener("mouseleave", () => su.classList.remove("show"));
    }
  });
}

function applyProviders(data) {
  $("#providers").replaceChildren();
  renderProviders();
}

// ── live classify ────────────────────────────────────────────────────────────

let tasksCache = null;
async function loadTasks() {
  const data = await api("/v1/tasks");
  tasksCache = data.tasks;
  $("#task").innerHTML = tasksCache.map((t) => `<option value="${esc(t.id)}">${esc(t.id)}</option>`).join("");
}

function renderPvButtons(data) {
  const ps = Object.values((data || lastProviders || { providers: {} }).providers).sort((a, b) => a.id.localeCompare(b.id));
  const avail = new Set(data ? data.enabled : []);
  $("#pvsel").innerHTML = ps.map((p) => {
    const on = data ? (avail.has(p.id) && p.available) : false;
    return `<button data-pid="${p.id}" class="${on ? "sel" : ""}"
      style="${on ? "" : "text-decoration:line-through"}"
      title="${esc(p.reason)}">${esc(p.id)}</button>`;
  }).join("");
  document.querySelectorAll("#pvsel [data-pid]").forEach((b) => b.addEventListener("click", () => {
    const p = ps.find((x) => x.id === b.dataset.pid);
    if (!p.available) {
      if (!confirm(`${p.id}: ${p.reason}\\nSelect it anyway?`)) return;
    }
    b.classList.toggle("sel");
  }));
}

function confBar(c) {
  if (c == null) return "";
  const v = Math.max(0, Math.min(1, Number(c)));
  const col = v >= 0.7 ? "var(--green)" : v >= 0.5 ? "var(--amber)" : "var(--red)";
  return `<span class="bar"><i style="width:${(v * 100).toFixed(0)}%;background:${col}"></i></span>${v.toFixed(2)}`;
}

async function runClassify() {
  const providers = [...document.querySelectorAll("#pvsel .sel")].map((b) => b.dataset.pid);
  const text = $("#text").value;
  if (!providers.length) { $("#result").innerHTML = '<div class="err">pick at least one provider</div>'; return; }
  if (!text.trim()) { $("#result").innerHTML = '<div class="err">nothing to classify — paste text first</div>'; return; }
  $("#run").disabled = true;
  $("#result").innerHTML = '<div class="tele">running…</div>';
  const body = { provider: providers.length === 1 ? providers[0] : providers, task: $("#task").value,
                 text, confidence_floor: parseFloat($("#floor").value) || 0.7, log: $("#logit").checked };
  let res;
  try { res = await api("/v1/classify", { method: "POST", body: JSON.stringify(body) }); }
  catch (e) { $("#result").innerHTML = `<div class="err">${esc(e.message)}</div>`; $("#run").disabled = false; return; }
  $("#run").disabled = false;
  renderResult(res, providers);
}

function renderResult(res, providers) {
  const vc = res.verdict === "pass" ? "v-pass" : res.verdict === "block" ? "v-block"
    : res.escalated ? "v-escalate" : "v-error";
  let html = `<div class="verdict ${vc}">${esc(res.verdict)}</div>
    <span class="tele">${res.latency_ms}ms · sha ${esc(res.input_sha256 || "")}${res.reason ? " · " + esc(res.reason) : ""}</span>
    <div class="tele" style="margin:2px 0 10px">${(res.summary || []).map(esc).join(" · ")}</div>`;
  const provs = Object.keys(res.answers || {});
  for (const prov of provs) {
    const tele = (res.telemetry || {})[prov] || {};
    html += `<table><tr><th colspan="3">${esc(prov)}
      <span class="tele">— http ${esc(tele.http_status ?? "-")} · ${esc(tele.latency_ms ?? "-")}ms
      · ${esc(tele.token_total ?? "-")} tok · retries ${esc(tele.retries ?? 0)}</span></th></tr>`;
    for (const [head, a] of Object.entries(res.answers[prov])) {
      if (head.startsWith("_")) continue;
      const label = a.label ?? a.score ?? a.noul ?? "(declined)";
      html += `<tr><td style="width:34%"><span class="tele">${esc(head)}</span></td>
        <td><b>${esc(label)}</b> ${confBar(a.confidence)}</td>
        <td><span class="exp"><input id="exp-${prov}-${head}" placeholder="expected…">
        <button onclick="saveFeedback('${esc(prov)}','${esc(head)}')">✓</button></span></td></tr>`;
    }
    html += "</table>";
  }
  $("#result").innerHTML = html;
  window._lastRes = res;
}

async function saveFeedback(prov, head) {
  const res = window._lastRes; if (!res || !res.input_sha256) return;
  const a = res.answers[prov] && res.answers[prov][head]; if (!a) return;
  const actual = $("#exp-" + prov + "-" + head).value.trim();
  if (!actual) { alert("type the expected label first"); return; }
  const predicted = String(a.label ?? a.score ?? a.noul ?? "");
  try {
    await api("/v1/feedback", { method: "POST", body: JSON.stringify({
      input_sha256: res.input_sha256, task: res.task, head_id: head,
      predicted_label: predicted, predicted_confidence: a.confidence ?? null,
      provider: prov, actual_label: actual, escalated: !!res.escalated }) });
    $("#exp-" + prov + "-" + head).style.borderColor = "var(--green)";
    setTimeout(() => { $("#exp-" + prov + "-" + head).style.borderColor = ""; }, 1200);
  } catch (e) { alert("feedback failed: " + e.message); }
}

// ── recent ───────────────────────────────────────────────────────────────────

async function renderRecent() {
  let data;
  try { data = await api("/v1/recent?limit=15"); }
  catch (e) { $("#recent").innerHTML = `<tr><td class="err">${esc(e.message)}</td></tr>`; return; }
  if (!data.rows.length) { $("#recent").innerHTML = "<tr><td>no events yet</td></tr>"; return; }
  const fmt = (ts) => { try { return new Date(ts).toLocaleString(); } catch { return ts; } };
  $("#recent").innerHTML = "<tr><th>time</th><th>op</th><th>task</th><th>providers</th><th>verdict</th><th>ms</th><th>labels</th></tr>"
    + data.rows.map((r) => `<tr>
      <td class="tele">${esc(fmt(r.ts))}</td>
      <td>${esc(r.op)}</td>
      <td>${esc(r.task)}</td>
      <td class="tele">${esc((r.providers_used || []).join(",") || r.provider || "")}</td>
      <td>${esc(r.verdict || "")}${r.escalated ? " ▲" : ""}</td>
      <td class="tele">${esc(r.latency_ms ?? "")}</td>
      <td style="color:var(--dim)">${esc(r.labels || r.error || "")}</td>
    </tr>`).join("");
}

// ── boot ─────────────────────────────────────────────────────────────────────

async function refreshAll() {
  try { await renderProviders(); renderPvButtons(lastProviders); } catch (e) { $("#providers").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  loadTasks().catch((e) => console.warn(e));
  renderRecent().catch((e) => console.warn(e));
}

$("#run").addEventListener("click", runClassify);
$("#refresh").addEventListener("click", refreshAll);
refreshAll();
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    return INDEX_HTML
