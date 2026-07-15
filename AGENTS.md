# AGENTS.md — Argus Multi-Agent Coordination

This file is the coordination contract for any AI agent (interactive Claude Code session,
Myrmidon swarm worker, or Agamemnon-orchestrated agent) that reads or modifies this
repository. Read it before touching any file.

---

## Repo Role in the HomericIntelligence Ecosystem

Argus is a **read-only observability layer**. It scrapes metrics and tails logs from
ProjectAgamemnon, ProjectNestor, NATS, Nomad, and all running containers. It does **not**
push data or commands back to any external service.

**Permitted write targets** (within this repo only):

| Path | Description |
| ------ | ------------- |
| `configs/` | Prometheus, Loki, Promtail, Grafana config files |
| `dashboards/` | Grafana dashboard JSON |
| `rules/` | Prometheus alerting rules |
| `justfile` | Task runner commands |
| `pixi.toml` | Python / tool dependency spec |
| `AGENTS.md`, `CLAUDE.md` | Coordination and project docs |

Agents **MUST NOT** modify `docker-compose.yml` network topology, external service configs
(Agamemnon, Nestor, NATS, Nomad), or anything outside this repository.

---

## Agent Access Model

Three agent classes operate on this repo:

| Class | Spawned By | Write Access | Notes |
| ------- | ----------- | -------------- | ------- |
| **Interactive** | Human via Claude Code CLI/IDE | Yes (user-confirmed) | Full permission scope |
| **Myrmidon swarm worker** | `hephaestus:myrmidon-swarm` | Worktree only | `isolation: "worktree"` required |
| **Agamemnon-orchestrated** | Remote trigger from Agamemnon | Read-only | Scrape validation and metric checks only |

---

## Available Skills (Hephaestus Plugin)

Argus delegates orchestration to the **Hephaestus plugin**. There is no `.claude/agents/`
directory in this repo. Use these skills instead:

| Skill | Trigger Condition | Description |
| ------- | ------------------ | ------------- |
| `hephaestus:advise` | Before unfamiliar work or unknown errors | Searches team knowledge base for prior learnings |
| `hephaestus:learn` | After experiments or novel discoveries | Saves session learnings as a new skill |
| `hephaestus:myrmidon-swarm` | Multi-step parallel file changes | Hierarchical delegation (Opus → Sonnet → Haiku) |
| `hephaestus:repo-analyze` | Full quality audit (15 dimensions) | Graded audit; use before major releases |
| `hephaestus:repo-analyze-quick` | Rapid health check — showstoppers only | Grade B baseline; flags broken/missing |
| `hephaestus:repo-analyze-strict` | Quality gate for release candidates | Starts at F; requires concrete evidence |

**Recommended flow:** Run `/hephaestus:advise` before any unfamiliar work. Run
`/hephaestus:learn` after discovering something non-obvious so the next agent benefits.

---

## Model-Tier Assignments

| Task Class | Model Tier | Rationale |
| ----------- | ----------- | ----------- |
| Architecture decisions, cross-service impact analysis | L0/L1 — Opus | Strategic reasoning across many constraints |
| Config authoring, dashboard JSON, alert rules, code review | L2/L3 — Sonnet | Domain specialist; cost-effective |
| Boilerplate YAML edits, single-file formatting, lint fixes | L4/L5 — Haiku | Focused, fast, cheap |

When using `hephaestus:myrmidon-swarm`, the orchestrator (Opus) decomposes the task and
delegates leaf work to Sonnet/Haiku workers. Do not override model tiers without explicit
user approval.

---

## Conflict-Avoidance Protocol

1. **Worktree isolation is mandatory** for all file-modifying swarm agents. Use
   `isolation: "worktree"` in every `Agent(...)` call that touches the filesystem.
2. The `.worktrees/` directory is the designated landing zone. One issue → one worktree.
3. No two agents may hold a write lock on the same file simultaneously within a wave.
4. Before opening any file for editing, run `git status` to detect uncommitted changes.
5. If an unexpected file modification is found, stop and report to the user before proceeding.

---

## Mandatory Approval Gate

Before any agent spawns subordinates that modify files, the following three-phase sequence
is **required**:

1. **Phase 1 — Exploration**: Read-only research agents gather facts (no file writes).
2. **Phase 2 — Design**: Orchestrator produces a plan and presents it to the user.
3. **Phase 3 — Execution**: User confirms ("yes", "go ahead", or equivalent) before any
   write tool is called.

Skipping or collapsing phases 1–2 into phase 3 is explicitly prohibited. An agent that
writes files without user confirmation has violated this protocol.

---

## Wave Execution Constraints

| Constraint | Value |
| ----------- | ------- |
| Max agents per wave | 5 |
| Shared file writes within a wave | Not permitted |
| Each agent write scope | Its own worktree only |
| CI requirement before merge | All checks must pass |

Waves run concurrently but must not overlap on the same file. Design task decomposition so
each wave agent owns a disjoint set of files.

---

## Handoff Protocol

When handing off between agents (e.g., end of context, model swap, swarm worker completion):

1. Record the following in the issue tracker file
   (`.issue_implementer/<issue-number>.json` if it exists):
   - Current branch name
   - Active worktree path
   - Output of `git log --oneline -5`
   - Summary of completed and remaining steps
2. Leave no uncommitted changes in the worktree. Either commit or stash before handoff.
3. The receiving agent reads this file first before taking any action.

---

## Verification Commands

```bash
# Confirm no local agent definitions exist (should return nothing)
grep -r "^level:" .claude/agents/*.md 2>/dev/null | sort | uniq -c

# List active worktrees
ls .worktrees/ 2>/dev/null || echo "(no active worktrees)"

# Check stack health
just status

# Verify scrape targets are reachable
just test-scrape
```

## AGENTS.md — Multi-Agent Coordination for Argus

This file describes how AI agents and automated tooling should interact with this
repository. It follows the conventions established across HomericIntelligence projects.

## Repository Role

Argus is a **read-only observability consumer**. It scrapes metrics from
other HomericIntelligence services and does not write to or modify them. Agents
working in this repository should maintain this invariant.

## Scope of Automated Changes

Agents are permitted to make the following changes autonomously:

| Area | Permitted |
| ------ | ----------- |
| `configs/prometheus.yml` — add/edit scrape jobs | Yes |
| `configs/loki.yml` — adjust retention, limits | Yes |
| `configs/promtail.yml` — add log scrape targets | Yes |
| `dashboards/*.json` — add or update Grafana dashboards | Yes |
| `rules/*.yml` — add or update alert and recording rules | Yes |
| `exporter/exporter.py` — add metrics, fix bugs | Yes |
| `tests/` — add or update tests | Yes |
| `docker-compose.yml` — add/remove services or env vars | **Review required** |
| `.github/workflows/` — CI changes | **Review required** |
| Credentials, secrets, `.env` | **Never** |

## Coordination Protocol

When multiple agents work on this repository simultaneously:

1. **One scrape target per PR** — Do not bundle unrelated Prometheus job additions.
2. **Metric names are stable API** — Never rename an existing metric without a
   deprecation period and dashboard update in the same PR.
3. **Alert rules must not regress** — `just validate` must pass before merging.
4. **Test coverage** — Any change to `exporter/exporter.py` must include or update
   tests in `tests/test_exporter.py`. `just test` must pass.

## Validation Gates

Before opening a PR, agents must verify:

```bash
just validate          # docker compose config + YAML lint
just test              # pytest unit tests
pixi run ruff check exporter/exporter.py
pixi run bandit -ll exporter/exporter.py
```

## Prohibited Actions

- Do not commit `.env` or any file containing credentials.
- Do not modify external service configurations (Agamemnon, Nestor, NATS).
- Do not remove existing alert rules without explicit instruction.
- Do not tighten the Prometheus scrape interval below 10s.
- Do not expose internal ports (Loki 3100, Prometheus 9090) beyond localhost.
