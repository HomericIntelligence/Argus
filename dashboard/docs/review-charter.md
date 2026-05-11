# Atlas Review-Wave Charter

> **Status:** Active. Referenced by every Atlas milestone PR template
> (`atlas-M1.md` … `atlas-M6.md` and any future `atlas-M*` template).
> **Owner:** Atlas team (HomericIntelligence/ProjectArgus).
> **Last updated:** 2026-05-05.

This document is the normative specification for the Atlas milestone review-wave
gate. It defines the six review dimensions, the criteria each reviewer must
verify, the verdicts they may return, and how the verdicts roll up to a
merge-or-block decision.

The automation that implements this charter lives in:

- `dashboard/scripts/atlas-review-dispatch.sh` — creates a review team in
  Agamemnon and files one task per dimension.
- `dashboard/scripts/atlas-review-aggregate.sh` — polls the team's task results
  and exits 0 only when all six dimensions return `approved`.
- `justfile` recipes `atlas-review-dispatch` and `atlas-review-aggregate`
  invoke the scripts.

A milestone PR is **only mergeable** when the aggregate script exits 0 — i.e.,
all six dimensions below have returned `approved`.

---

## Workflow

1. Author opens an Atlas milestone PR using one of the `atlas-M*.md`
   templates.
2. CI runs (lint, build, test, e2e). Author confirms the CI checkbox.
3. Author dispatches the review wave: `just atlas-review-dispatch <Mn> <PR_URL>`.
   Six tasks (one per dimension) are filed against a fresh Agamemnon team.
4. Reviewers (human or agent) work each dimension's task. Each reviewer
   produces a verdict (see [Verdicts](#verdicts) below) and posts it as the
   task's `result` field via Agamemnon's `/v1/tasks/{id}/complete` endpoint.
5. Author runs `just atlas-review-aggregate <Mn> <TEAM_ID>`. The script:
   - Fetches the six tasks.
   - Reads `.result` from each.
   - Exits 0 if all six are `approved`, non-zero otherwise.
6. Author updates the PR template's "Review Wave" table with the per-dimension
   status. PR is mergeable when all six rows show `✅ approved`.
7. After merge, author bumps the Odysseus submodule pin.

---

## Verdicts

Every reviewer returns exactly one of:

| Verdict | Meaning | Effect |
| --- | --- | --- |
| `approved` | The dimension's criteria are met. | Counts toward the 6/6 needed to merge. |
| `changes-requested` | At least one criterion is not met. | Author must address findings, then re-dispatch the wave (new team). |
| `not-applicable` | The dimension genuinely does not apply to this PR (e.g., no UX surface). | Treated as `approved`. **Must include a one-sentence justification** in the task result body. |

`changes-requested` and `not-applicable` verdicts must include a brief written
explanation. `approved` may stand alone.

---

## The six dimensions

Each dimension below states (a) the reviewer's responsibility, (b) the criteria
that must hold for an `approved` verdict, and (c) what evidence the reviewer
should cite when posting the result.

### 1. `arch` — Architecture

**Responsibility:** Verify that the change preserves Atlas's documented
architecture (single static binary on `:3002`; layered `cmd → server → handlers
→ store/poller/nats`; in-process events.Bus; SSE fan-out; pollers in goroutines
with context cancellation).

**Approval criteria:**

- No new circular imports between internal packages.
- New code follows the existing layering (handlers do not import directly from
  `nats`; pollers do not import from `handlers`; etc.).
- Goroutine lifecycle: every new long-running goroutine respects `ctx.Done()`
  and is launched from `cmd/argus-dashboard/main.go` (the composition root).
- Interface boundaries used where multiple implementations exist (e.g., the
  Tailscale `Source` pattern); concrete types only when there is exactly one
  implementation.
- Configuration validated at `config.Config.Validate` — no silent defaults for
  security-relevant settings.

**Evidence:** file:line references to layering, lifecycle, and interface usage.

### 2. `code` — Source Code Quality

**Responsibility:** Verify that the change is readable, idiomatic Go, and free
of foot-guns.

**Approval criteria:**

- Errors wrapped with `%w` where they cross package boundaries; errors not
  silently swallowed (no new `_ = err`, no new `//nolint:errcheck` without a
  written justification).
- Structured logging via `slog` (no `log.Print*`, no `fmt.Println` in
  production code).
- HTTP clients have explicit timeouts.
- Goroutines are bounded (worker pools or fixed fan-out).
- Resource handles closed on every path (deferred `Close`, `defer cancel`).
- No new magic numbers — durations and counts come from `config` or named
  constants.

**Evidence:** file:line references to error handling and logging in the
changed lines.

### 3. `security` — Security

**Responsibility:** Verify that the change does not weaken Atlas's security
posture.

**Approval criteria:**

- No new endpoints mounted outside the auth `Group(...)` in
  `internal/server/routes.go` other than `/livez` (which must always return 200
  for k8s liveness).
- All operator-supplied URLs that flow into HTML attributes or HTTP headers
  pass `config.validateIframeURL` (or equivalent) — no raw concatenation into
  CSP or `src`.
- Constant-time comparison for any secret comparison
  (`subtle.ConstantTimeCompare`).
- No new dependencies with known vulnerabilities (`govulncheck ./...` clean).
- No secrets, tokens, or PII written to logs.
- Markdown rendering keeps `goldmark` `WithUnsafe()` disabled.

**Evidence:** `govulncheck` output, `gosec` output, file:line for any
auth/CSP/iframe surface touched.

### 4. `ux` — User Experience

**Responsibility:** Verify that the change is usable by operators without
training.

**Approval criteria:**

- New user-visible pages render with non-empty data on the empty / cold-start
  case (no blank panels with no explanation).
- Error states for upstream failures show a human-readable message (not a
  blank `500`).
- Keyboard navigation works on new interactive surfaces.
- Page updates from SSE/htmx do not flicker or lose scroll position on swap.
- Verdict `not-applicable` is the right choice if the PR does not touch any
  template, static asset, or HTTP response body.

**Evidence:** screenshot or terminal capture of the page in cold-start and
upstream-down states.

### 5. `ops` — Operations

**Responsibility:** Verify that the change is operable in production — that it
exposes the signals operators need and degrades gracefully.

**Approval criteria:**

- Every new long-running component is reflected in `/readyz` (loaded, enabled,
  and reporting valid results without error). If the component is intentionally
  optional (e.g., Tailscale API mode when `ATLAS_TAILSCALE_API_KEY` is unset),
  `/readyz` reports it as `ok: true, note: "disabled"` rather than failing.
- Every new error path increments a metric registered in
  `internal/server/metrics.go`.
- Every new external call has a timeout and a retry/backoff plan or an
  explicit decision not to retry.
- `rules/atlas-alerts.yml` covers any new error counter that should page.
- Graceful shutdown: new goroutines drain on `ctx.Done()` within the existing
  10s shutdown window.

**Evidence:** file:line for the readyz hook, the metric increment site, and
the alert rule.

### 6. `docs` — Documentation

**Responsibility:** Verify that user-visible and operator-visible behaviour is
documented in the right place.

**Approval criteria:**

- New environment variables are listed in `dashboard/README.md` with default
  and purpose.
- Breaking changes are noted in `CHANGELOG.md` under the next release.
- New HTTP endpoints are listed in `dashboard/README.md`'s endpoint table.
- New SSE topic semantics are documented in the SSE protocol section.
- This charter is updated when the dimension criteria themselves change.

**Evidence:** diff of the README / CHANGELOG entry.

---

## Re-dispatching the wave

If any dimension returns `changes-requested`, the author addresses the
findings, pushes a new commit, and runs `just atlas-review-dispatch <Mn>
<PR_URL>` again. **The aggregate is per-team, not per-PR** — each dispatch
creates a fresh team, so prior verdicts are not carried over.

When a reviewer's first verdict is `not-applicable`, they should not need to
re-review on subsequent waves unless the PR has grown into the dimension's
scope (e.g., a `code`-only change has added a new HTTP endpoint and now needs
`security` and `ops` re-review).

---

## Conflict resolution

If two reviewers post conflicting verdicts on the same dimension's task, the
later verdict wins (it's the only one read by the aggregate script). Reviewers
who disagree with an `approved` verdict should:

1. Open a GitHub issue tagged `atlas-review-dispute` with the team_id and
   dimension.
2. Block the PR via a GitHub review (`changes-requested`).
3. The author addresses the dispute and re-dispatches.

---

## Why these six?

The six dimensions are deliberately chosen so that **at most two reviewers
need to coordinate per PR**: most changes touch one or two dimensions
substantively and the rest pass with `not-applicable`. The set is closed:
adding a seventh dimension would require updating this charter and the
dispatch script in lockstep.
