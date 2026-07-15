# Atlas Release Rollback Runbook

You just promoted an Atlas release and something is wrong. This runbook gets you reverted to the previous known-good
tag without reading the rest of the repo. For general diagnosis (NATS, pollers, SSE, auth) see
[`../runbook.md`](../runbook.md); this document is narrowly about **revert the bad release I just shipped**.

The current latest release is `v0.2.0`. The patch series under `fix/atlas-v0.2.1-security` will ship as `v0.2.1` —
both tag names appear below in the worked examples.

## When to roll back

Roll back when the failure correlates with the deploy and the previous tag was healthy. Concrete signals:

- `/readyz` returns `503` from a freshly-deployed image that previously stayed `200`.
- `atlas_nats_connected` is flat at `0` for more than ~30 s after the new container reaches `running`.
- A spike in 401s in the access log immediately after rollout (auth regression).
- `atlas_poll_errors_total{source=...}` jumps and the `AtlasPollErrorsHigh` alert fires within minutes of deploy.
- SSE clients see only `: heartbeat` frames and `atlas_sse_connected_clients` is non-zero (subscriber wedged).
- The Atlas process panics on startup or `docker compose ps` shows `restarting (1)` looping on `argus-dashboard`.

If two or more of those signals showed up **after** the deploy and **were not present before**, this runbook is the
right tool. If the same signals were present on the previous tag too, the problem is upstream — fix that first.

## Pre-rollback checklist

Spend two minutes confirming the deploy is the cause. A rollback does not help if NATS or Agamemnon is the actual
culprit and it loses you forensic state on the bad image.

```bash
TOKEN="$ATLAS_AUTH_BEARER_TOKEN"
ATLAS=http://localhost:3002

# 1. Process up at all? /livez is unauthenticated and cheap.
curl -fsS -o /dev/null -w '%{http_code}\n' "$ATLAS/livez"

# 2. Which component is failing? /readyz JSON names the offender.
curl -fsS -H "Authorization: Bearer $TOKEN" "$ATLAS/readyz" | jq '.components[] | select(.ok == false)'

# 3. Is the upstream that /readyz blames actually reachable from the Atlas host?
curl -fsS "$ATLAS_AGAMEMNON_URL/v1/agents"   >/dev/null && echo agamemnon ok
curl -fsS "$ATLAS_NATS_MON_URL/varz"         >/dev/null && echo nats-mon  ok
curl -fsS "$ATLAS_NATS_MON_URL/jsz"          >/dev/null && echo jetstream ok
```

If `/livez` already returns non-200, the binary is broken on this tag — roll back. If `/livez` is 200 but `/readyz`
blames an upstream that you confirmed is **down** with a direct `curl`, fix the upstream; rolling Atlas back will not
revive a dead Agamemnon or NATS.

Capture a snapshot of the bad image's logs before you revert — the container disappears on `docker compose up -d`:

```bash
docker compose logs --no-color --tail=500 argus-dashboard > /tmp/atlas-bad-deploy-$(date -u +%Y%m%dT%H%M%SZ).log
```

## Rolling back via Docker Compose

The repository's `docker-compose.yml` pins the Atlas image at the `argus-dashboard` service. Edit that pin to the
previous tag, pull, and recreate. Other services (`argus-prometheus`, `argus-loki`, `argus-grafana`, `argus-promtail`)
are unaffected.

1. **Identify the previous good tag.** GitHub releases are immutable and ordered by publish time:

   ```bash
   gh release list --repo HomericIntelligence/Argus --limit 10
   ```

   Pick the most recent tag that is **not** the bad one. Example: bad = `v0.2.1`, good = `v0.2.0`.

2. **Edit `docker-compose.yml`.** Find the line `image: ghcr.io/homericintelligence/atlas:<bad-tag>` under the
   `argus-dashboard:` service and replace `<bad-tag>` with the previous good tag. Pin explicitly — do not use
   `:latest`. Reproducibility matters more during a rollback than at any other time.

   ```diff
   -    image: ghcr.io/homericintelligence/atlas:v0.2.1
   +    image: ghcr.io/homericintelligence/atlas:v0.2.0
   ```

3. **Pull and recreate just the Atlas service.** No need to disturb Prometheus, Loki, Grafana, or Promtail:

   ```bash
   docker compose pull argus-dashboard
   docker compose up -d argus-dashboard
   docker compose logs -f --tail=50 argus-dashboard
   ```

4. **Watch recovery.** `/livez` should return 200 within a few seconds of the container reaching `running`; `/readyz`
   should return 200 within ~10–20 s as the in-memory caches rebuild from upstream (~2 poll intervals at the default
   5 s tick).

   ```bash
   # poll until both are 200, then stop
   while true; do
     L=$(curl -sS -o /dev/null -w '%{http_code}' "$ATLAS/livez")
     R=$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$ATLAS/readyz")
     printf 'livez=%s readyz=%s\n' "$L" "$R"
     [ "$L" = "200" ] && [ "$R" = "200" ] && break
     sleep 2
   done
   ```

   No restart of NATS, Agamemnon, or any other upstream is required — Atlas is stateless and rebuilds its caches by
   re-polling.

5. **Commit the rollback.** The compose file lives in git. Push the pin change so the next operator sees the truth:

   ```bash
   git checkout -b ops/rollback-atlas-$(date -u +%Y%m%d)
   git add docker-compose.yml
   git commit -m "ops: roll back atlas to v0.2.0 (v0.2.1 was bad)"
   git push -u origin HEAD
   ```

## Rolling back via Kubernetes

If you run Atlas in Kubernetes (most operators run via Compose; this section is short on purpose):

```bash
kubectl rollout history deployment/atlas
kubectl rollout undo deployment/atlas                          # to the previous revision
kubectl rollout undo deployment/atlas --to-revision=<N>        # to a specific revision
kubectl rollout status deployment/atlas
kubectl logs -f deployment/atlas --tail=50
```

Verify with the same `/livez` / `/readyz` poll loop shown above — the readiness contract is identical regardless of
runtime. If the deployment manifest is GitOps-managed (Argo CD, Flux), update the image tag in the source repo
instead of using `kubectl set image`, otherwise the next reconcile will roll forward to the bad tag.

## Verifying recovery

You are not done until **all** of the following are true:

- `curl -fsS "$ATLAS/livez"` returns `200`.
- `curl -fsS -H "Authorization: Bearer $TOKEN" "$ATLAS/readyz"` returns `200`, or — if an upstream really is down —
  the `components` array shows the same set of OK components as before the bad deploy (no new regressions).
- `atlas_nats_connected == 1` (when NATS is reachable):

  ```bash
  curl -fsS -H "Authorization: Bearer $TOKEN" "$ATLAS/metrics" | grep '^atlas_nats_connected '
  ```

- The image tag actually loaded matches what you wrote into `docker-compose.yml`:

  ```bash
  docker compose config | grep -E 'image:.*atlas'
  docker inspect argus-dashboard --format '{{.Config.Image}}'
  ```

  Both must show the rolled-back tag (e.g. `ghcr.io/homericintelligence/atlas:v0.2.0`).

- At least one fresh request appears in the access log after rollback — proves the new binary is actually serving:

  ```bash
  docker compose logs --tail=10 argus-dashboard | grep -E '"GET /(livez|readyz|metrics)'
  ```

## Tagging the bad release as broken

Once the rollback is stable, mark the bad release on GitHub so nobody re-deploys it by accident. The tag itself is
immutable — you are only editing the release notes:

```bash
gh release edit v0.2.1 --repo HomericIntelligence/Argus \
  --notes 'KNOWN BAD — do not deploy. Rolled back $(date -u +%F). See dashboard/docs/runbook/rollback.md.'
```

If a CI/CD pipeline auto-promotes the latest release, also flip "Latest release" off this tag in the GitHub UI
(`Releases → v0.2.1 → Edit → uncheck "Set as the latest release"`) and set the previous good tag as latest. `gh
release edit --latest=false` does the same thing non-interactively if your `gh` is recent enough.

Open a tracking issue immediately, even if you do not yet know the root cause:

```bash
gh issue create --repo HomericIntelligence/Argus \
  --title 'Atlas v0.2.1 rolled back — root cause TBD' \
  --label 'area:atlas,severity:high' \
  --body 'Rolled back to v0.2.0 at <time>. Bad-deploy logs in /tmp/atlas-bad-deploy-*.log on <host>. RCA pending.'
```

## v0.2.0 caveats that affect rollback

Read these before rolling forward to v0.2.0 specifically.

### `MaxReconnects(0)` — subscriber will not self-recover

The v0.2.0 NATS client is configured with `MaxReconnects(0)`, which **disables reconnection**. If the bad deploy
held a NATS connection that dropped during rollout, the rolled-back v0.2.0 image will **also** stay disconnected —
the bug is in v0.2.0 too. After rollback, restart the rolled-back container once if `atlas_nats_connected == 0`:

```bash
docker compose restart argus-dashboard
# Then re-run the recovery verification above.
```

The fix lands in v0.2.1 (proper reconnect backoff). Until then, treat any NATS blip after a rollback as an
expected restart trigger, not a bug in the rollback procedure.

### Rolling back from v0.2.1 to v0.2.0 reopens fixed security bugs

> **Loud warning.** v0.2.1 contains two security fixes. Rolling back to v0.2.0 **reopens both** for as long as the
> rolled-back container runs. Decide consciously, ideally with security on the call.

- **Case-mismatch auth bypass** (`ATLAS_AUTH_MODE`). v0.2.0's middleware compared the auth-mode string exactly, so a
  mixed-case value such as `Bearer` or `BEARER` fell through to the default branch and disabled auth. Anyone who can
  set the env var to a non-canonical case can serve Atlas unauthenticated. Mitigation while rolled back: pin
  `ATLAS_AUTH_MODE: bearer` (lowercase) in `docker-compose.yml` and audit any process that templates that value.
- **SSE token leak in access log.** v0.2.0's `middleware.Logger` writes the full request URI — including
  `?token=<token>` query strings used by EventSource clients — to stdout, which Promtail ships to Loki. Mitigation
  while rolled back: rotate any bearer token that has been used by a browser-based SSE client since the rollback,
  and tighten access to Loki for the duration.

If the bad release **is** v0.2.1 and the failure mode is not security-related, prefer a forward-fix
(`fix/atlas-v0.2.1-security` patch on top) over reverting to v0.2.0. The security exposure of v0.2.0 is usually
worse than whatever functional regression v0.2.1 introduced.

## Forward-fix vs roll-forward

**Default is revert.** Atlas is a read-only observability dashboard — rolling back loses no data, breaks no
upstream contract, and the in-memory cache rebuilds in seconds. A short outage on `/agents` or `/events` is cheaper
than a long debug session on a live-broken release.

Choose forward-fix only when:

- The previous tag has a known security regression worse than the current bug (see the v0.2.1 → v0.2.0 warning
  above), **and**
- You have a one-line patch with a CI-green PR ready to merge in the next 15 minutes, **and**
- The current failure mode is not actively paging or breaking the operator's view of the rest of the mesh.

Otherwise: revert now, investigate from the captured bad-deploy logs, ship the real fix as the next patch release.

## Escalation

- **Security regression in the bad release** (auth bypass, token leak, RCE, anything CVE-shaped): follow
  [`SECURITY.md`](../../../SECURITY.md) at the Argus root for responsible disclosure and coordinated
  disclosure timing. Do not file a public GitHub issue with reproduction details.
- **Functional regression**: open a GitHub issue on `HomericIntelligence/Argus` tagged `area:atlas` and link
  the captured bad-deploy log file from the pre-rollback step.
- **Architecture questions** (what each component is supposed to do on startup, what `/readyz` covers):
  [`../architecture.md`](../architecture.md).
- **General diagnosis** (NATS subscriber, pollers, SSE, auth — anything that is not specifically about a bad
  release): [`../runbook.md`](../runbook.md).
