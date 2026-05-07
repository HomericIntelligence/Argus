# Atlas Operational Runbook

This runbook covers production diagnosis and recovery for Atlas v0.2.0+. It assumes you have:

- Shell access to a host that can reach the Atlas container (port 3002 by default).
- The configured bearer token (or auth disabled — see [Authentication](../README.md#authentication)).
- Read access to the container's stdout/stderr stream (Compose, Promtail/Loki, k8s, etc.).

## First-look commands

The two most useful endpoints are `/livez` (process up, always 200, **unauthenticated**)
and `/readyz` (component aggregator, **auth-gated**, returns structured JSON).

```bash
TOKEN="$ATLAS_AUTH_BEARER_TOKEN"
ATLAS=http://localhost:3002

# Process-up check
curl -fsS "$ATLAS/livez" && echo

# Component readiness — JSON aggregator
curl -fsS -H "Authorization: Bearer $TOKEN" "$ATLAS/readyz" | jq .

# Live counters (Prometheus exposition)
curl -fsS -H "Authorization: Bearer $TOKEN" "$ATLAS/metrics" \
  | grep -E '^atlas_(nats_connected|sse_connected_clients|poll_errors_total|sse_dropped_total)'
```

`/readyz` returns:

```json
{
  "ok": true,
  "components": [
    {"name": "agamemnon-poller", "ok": true,  "last_success": "2026-05-06T12:34:01Z"},
    {"name": "nats-poller",      "ok": true,  "last_success": "2026-05-06T12:34:00Z"},
    {"name": "nats-subscriber",  "ok": true,  "note": "attached=6"}
  ]
}
```

`ok: false` on any component flips the top-level `ok` to `false` and the response
status to `503`. The component name and `error` / `note` fields tell you where to look.

---

## NATS subscriber not attached

**Symptoms**

- `/readyz` JSON shows `nats-subscriber: ok=false` (often with `attached=0` or a connect error).
- `atlas_nats_connected` metric is `0`.
- `/events` SSE clients receive only `: heartbeat` comments, no real events.

**Likely causes & checks**

1. **NATS URL wrong / unreachable.** Run `curl -fsS $ATLAS_NATS_MON_URL/varz` from inside
   the Atlas container's network namespace. If that fails, the subscriber cannot connect either.
2. **JetStream not enabled.** Atlas requires JetStream — start nats-server with `-js`. Confirm
   via `curl $ATLAS_NATS_MON_URL/jsz | jq .config`.
3. **Streams missing.** Atlas attaches durable consumers on six streams: `homeric-agents`,
   `homeric-tasks`, `homeric-myrmidon`, `homeric-research`, `homeric-pipeline`, `homeric-logs`.
   If any are missing, `/readyz` shows a partial attach (`attached=N` where `N<6`). Provision
   them on the upstream NATS — Atlas does not auto-create streams.
4. **Credentials.** If your NATS requires auth, set `ATLAS_NATS_URL` to include credentials
   (`nats://user:pass@host:4222` — note this leaks via process listings; prefer NATS NKey/JWT).

**Recovery**

> **Important caveat — known v0.2.0 limitation:** the NATS client is configured with
> `MaxReconnects(0)`, which **disables reconnection**. After a connection is lost, Atlas does
> not retry — it stays in the disconnected state until the process is restarted.
> *(Tracked for v0.2.1; do not waste time waiting for self-recovery.)*

Once the upstream issue is resolved, restart Atlas:

```bash
docker compose restart argus-dashboard
# or, in k8s:
kubectl rollout restart deployment/atlas
```

After restart, `/readyz` should report `attached=6` within a few seconds. The in-memory
caches rebuild from upstream within ~2 poll intervals (~10–20 s).

---

## Pollers reporting stale data

**Symptoms**

- `/readyz` JSON shows `agamemnon-poller` or `nats-poller` with `ok=false` and an
  error string like `"stale: last success was 47s ago"`.
- `/agents` page shows old data; cache has not refreshed.
- `atlas_poll_errors_total{source="..."}` counter increasing.

**Staleness budget**

A poller is considered "ready" when its last success is within **2× the configured poll
interval** (default poll = 5 s, so the budget is ~10 s). Beyond that, `/readyz` reports stale.

**Likely causes & checks**

1. **Upstream service is down.** Atlas reads via plain HTTP — try the same URL Atlas uses:

   ```bash
   curl -fsS "$ATLAS_AGAMEMNON_URL/v1/agents"
   curl -fsS "$ATLAS_NATS_MON_URL/varz"
   curl -fsS "$ATLAS_NATS_MON_URL/jsz"
   ```

   If those fail from the Atlas host, fix the upstream first.
2. **Slow upstream.** Atlas uses 3 s HTTP timeouts on outbound polls. A consistently slow
   upstream (>3 s response) will look like a permanent error in metrics. Increase
   the upstream's capacity or move it closer to Atlas.
3. **DNS / routing change.** Compose service names (`agamemnon`, `nats`) only resolve inside
   the `argus` Docker network. Verify with `docker compose exec argus-dashboard ping -c1 nats`.

**Recovery**

Pollers self-heal: once the upstream is back, the next tick succeeds and `/readyz` flips
to `ok=true`. No restart needed.

---

## SSE clients only see heartbeats

**Symptoms**

- Browsers / SSE consumers receive only `: heartbeat` lines every 15 s.
- No `event: agent` / `event: task` / `event: nats` / `event: host` frames.

**Diagnosis**

This is almost always one of:

- **NATS subscriber not attached** → see that section above. The six NATS-derived topics
  (`agent`, `task`, `myrmidon`, `research`, `pipeline`, `log`) all originate there.
- **No upstream activity** — Atlas only fans out events that arrive from NATS or from its
  own pollers. If nothing is publishing, nothing arrives. Verify by publishing a test event:

  ```bash
  nats pub hi.agents.demo '{"id":"demo","status":"ok"}'
  ```

  An attached subscriber should immediately fan that out as `event: agent`.
- **Topic filter excluding everything** — clients can constrain via `?topics=agent,task`.
  Drop the parameter to subscribe to all eight topics (six NATS-derived plus `nats`/`host`
  internal).

---

## Bearer-token errors / 401 loops

**Symptoms**

- `curl` returns `401 Unauthorized`, browsers loop on the auth challenge, scrape jobs error.

**Checks**

1. **Token actually set in environment of the running container?**
   `docker compose exec argus-dashboard env | grep -E '^ATLAS_AUTH'` — must show `ATLAS_AUTH_MODE=bearer`
   and a non-empty `ATLAS_AUTH_BEARER_TOKEN`.
2. **Header form correct?** Must be `Authorization: Bearer <token>` exactly (case-sensitive
   `Bearer`). For SSE / EventSource (which can't set headers), use `?token=<token>` query param.
3. **Token has whitespace/newline?** Common `.env` paste bug; verify with
   `docker compose exec argus-dashboard printenv ATLAS_AUTH_BEARER_TOKEN | xxd | head -1`.
4. **Trailing slash / redirect?** chi does not redirect 301; route paths must match exactly.

> **v0.2.0 caveat — case sensitivity in `ATLAS_AUTH_MODE`.** The middleware compares the
> auth-mode string exactly; mixed-case values like `Bearer` or `BEARER` will fall through
> to the default branch. Use lowercase `bearer`/`basic`/`none` only. *(Tracked for v0.2.1.)*

---

## High SSE drop counter (`atlas_sse_dropped_total`)

**Symptoms**

- `atlas_sse_dropped_total{subscriber=...}` counter rising.
- `AtlasSSEDropsHigh` Prometheus alert firing (>100 drops in 5 m).

**Cause**

A specific SSE client is consuming events slower than Atlas produces them. Each subscriber
gets a 1000-deep `chan events.Event` buffer; once full, new events go to `/dev/null` for
that one client. Other clients are unaffected.

**Checks**

1. `atlas_sse_connected_clients` — is one client stuck open and not draining?
2. Inspect with browser DevTools / `nats top`-style probe — is the consumer iterating?
3. Network path slow? Reverse-proxy buffering?

**Recovery**

Drop the slow connection client-side (close and reconnect). Atlas will replay up to the
ring-buffer size (256 events) on reconnect.

---

## Logs and structured field reference

Atlas uses Go `slog` with JSON output. Key structured fields:

| Field | Meaning |
|---|---|
| `level` | `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `msg` | One-line event description |
| `version` | Build-stamped via `-ldflags -X version.Version=...` |
| `auth_mode` | The configured auth mode at startup |
| `source` | Poller source name (`agamemnon`, `nats`, `tailscale`, …) |
| `error` | Wrapped error chain when present |
| `subject` | NATS subject for subscriber logs |
| `stream` | JetStream stream name |
| `attached` | Number of streams the subscriber successfully attached |

Filter in Loki via LogQL:

```logql
{job="atlas"} | json | level="ERROR"
{job="atlas"} | json | source="nats" | line_format "{{.msg}} {{.error}}"
```

> **Operator security note (v0.2.0):** chi's `middleware.Logger` writes the full request URI
> (including any query string) to stdout, so SSE clients that authenticate via `?token=<token>`
> have their token written to the container log and onward to Loki/Promtail. Treat your Loki
> instance accordingly until the token-scrubbing fix lands. *(Tracked for v0.2.1.)*

---

## Restart procedure

In-memory state (cache, ring buffer, agent event history) is lost on restart but
rehydrates from upstream within ~2 poll intervals. Persistent state lives in NATS JetStream
and the upstream services Atlas observes — Atlas itself is stateless.

```bash
# Compose
docker compose restart argus-dashboard
docker compose logs -f --tail=50 argus-dashboard

# Kubernetes
kubectl rollout restart deployment/atlas
kubectl rollout status deployment/atlas
kubectl logs -f deployment/atlas
```

Rollback to a prior tag:

```bash
# Pin to the previous immutable tag — never rely on :latest for rollbacks
docker compose up -d --pull always argus-dashboard  # after editing image: tag in compose
# or
kubectl set image deployment/atlas atlas=ghcr.io/homericintelligence/atlas:v0.1.0
```

After rollback, verify `/readyz` returns 200 and the metrics counters are advancing
before declaring the recovery complete.

---

## Escalation

- Security issues: see [`SECURITY.md`](../../SECURITY.md) at the ProjectArgus root —
  responsible disclosure with 48 h acknowledgement SLA.
- Functional bugs: open a GitHub issue tagged `area:atlas` against
  `HomericIntelligence/ProjectArgus`.
- Architecture questions: [`docs/architecture.md`](architecture.md) is the source of
  truth for component composition, concurrency, and resource bounds.
