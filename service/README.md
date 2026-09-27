# sys1 service

The HTTP wrapper around the [`sys1`](../) library. One container, every
language.

## Run locally

```bash
# from the repo root
uv sync --all-extras
uv run uvicorn service.main:app --host 127.0.0.1 --port 8400
```

Open the auto-generated OpenAPI docs at <http://127.0.0.1:8400/docs>.

## Run in Docker

```bash
docker build -t sys1-service -f service/Dockerfile .
docker run --rm -p 8400:8400 \
  -e SYS1_BEARER_TOKEN=$(openssl rand -hex 16) \
  -e FASTINO_API_KEY=$FASTINO_API_KEY \
  -e TYPESAFE_API_KEY=$TYPESAFE_API_KEY \
  -v $PWD/logs:/var/lib/sys1/logs \
  sys1-service
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/v1/classify`   | bearer¹ | Run providers for one payload |
| `POST` | `/v1/cascade`    | bearer¹ | Walk a provider chain |
| `GET`  | `/v1/health`     | none    | Per-provider availability |
| `GET`  | `/v1/tasks`      | none    | List registered tasks + heads |
| `GET`  | `/v1/tasks/{id}` | none    | One task's full definition |
| `POST` | `/v1/feedback`   | bearer¹ | Record a human-review outcome |
| `GET`  | `/v1/calibration`| bearer¹ | Confidence curve + agreement |
| `GET`  | `/healthz`       | none    | Liveness probe |

¹ Only enforced when `SYS1_BEARER_TOKEN` is set; otherwise the service is open.

## Request example

```bash
curl -sX POST http://127.0.0.1:8400/v1/classify \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SYS1_BEARER_TOKEN" \
  -d '{"provider":"decide","task":"change","text":"diff --git a/x.py b/x.py ..."}'
```

## Response example

```json
{
  "task": "change",
  "provider": "decide",
  "providers_used": ["decide"],
  "answers": {
    "decide": {
      "diff_type": {"type": "choice", "label": "feat", "confidence": 0.91},
      "risk_tier": {"type": "choice", "label": "low", "confidence": 0.82}
    }
  },
  "telemetry": {
    "decide": {"http_status": 200, "latency_ms": 142, "retries": 0, "structured_ok": true}
  },
  "verdict": "pass",
  "escalated": false,
  "latency_ms": 142,
  "summary": ["..."],
  "input_sha256": "ab12cd34ef56ab78"
}
```

## Auth

Set `SYS1_BEARER_TOKEN` in the environment. Every request must carry
`Authorization: Bearer <token>`. Without it, the service runs open — only
appropriate behind a private network or a reverse proxy that does its own auth.
