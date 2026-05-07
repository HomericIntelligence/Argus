# Contributing to ProjectArgus

Thank you for your interest in contributing to ProjectArgus! This is the observability stack
(Prometheus, Grafana, Loki) for the
[HomericIntelligence](https://github.com/HomericIntelligence) distributed agent mesh, with a
custom Python exporter for Agamemnon/Nestor/NATS metrics.

For an overview of the full ecosystem, see the
[Odysseus](https://github.com/HomericIntelligence/Odysseus) meta-repo.

## Quick Links

- [Development Setup](#development-setup)
- [What You Can Contribute](#what-you-can-contribute)
- [Development Workflow](#development-workflow)
- [Running and Testing](#running-and-testing)
- [Pull Request Process](#pull-request-process)
- [Code Review](#code-review)

## Development Setup

### Prerequisites

- [Git](https://git-scm.com/)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Podman](https://podman.io/) (or Docker) for running the observability stack
- [Pixi](https://pixi.sh/) for environment management
- [Just](https://just.systems/) as the command runner

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/HomericIntelligence/ProjectArgus.git
cd ProjectArgus

# Activate the Pixi environment
pixi shell

# Copy and customize environment variables
cp .env.example .env

# Start the observability stack
just start

# List available recipes
just --list
```

### Verify Your Setup

```bash
# Check stack status
just status

# Test Prometheus scraping
just test-scrape
```

### Pre-commit hooks

We use [pre-commit](https://pre-commit.com/) to catch formatting, linting, and templ-generate
drift before a commit lands. Install once per clone:

```bash
pixi run pre-commit install
```

The hooks defined in `.pre-commit-config.yaml` cover:

- **YAML/JSON/whitespace** — trailing whitespace, end-of-file, merge conflicts, `yamllint`.
- **Go (`dashboard/`)** — `gofmt -s -w`, `golangci-lint run` (v2.12.2 — matches CI), and a
  `templ generate` diff check (requires `templ` v0.3.1001 — `go install
  github.com/a-h/templ/cmd/templ@v0.3.1001`).
- **CI parity** — the Go hooks shell out to the same binaries CI runs, so a clean local
  pre-commit means a green Go-side CI; CI remains the authoritative gate.

## What You Can Contribute

- **Prometheus scrape configs** — New targets and scrape intervals
- **Grafana dashboards** — JSON dashboard definitions for new services
- **Alert rules** — Prometheus alerting rules and notification channels
- **Custom exporter** — Improvements to the Python exporter in `exporter/`
- **Docker Compose** — Stack configuration and service networking
- **Justfile recipes** — New management and diagnostic commands
- **Documentation** — README updates, dashboard usage guides

### Custom Exporter

The Python exporter (`exporter/exporter.py`) uses only the Python standard library — no
external dependencies. When contributing to the exporter, maintain this constraint.

## Development Workflow

### 1. Find or Create an Issue

Before starting work:

- Browse [existing issues](https://github.com/HomericIntelligence/ProjectArgus/issues)
- Comment on an issue to claim it before starting work
- Create a new issue if one doesn't exist for your contribution

### 2. Branch Naming Convention

Create a feature branch from `main`:

```bash
git checkout main
git pull origin main
git checkout -b <issue-number>-<short-description>

# Examples:
git checkout -b 8-add-nats-dashboard
git checkout -b 5-fix-prometheus-scrape-interval
```

**Branch naming rules:**

- Start with the issue number
- Use lowercase letters and hyphens
- Keep descriptions short but descriptive

### 3. Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**

| Type       | Description                |
|------------|----------------------------|
| `feat`     | New feature                |
| `fix`      | Bug fix                    |
| `docs`     | Documentation only         |
| `style`    | Formatting, no code change |
| `refactor` | Code restructuring         |
| `test`     | Adding/updating tests      |
| `chore`    | Maintenance tasks          |

**Example:**

```bash
git commit -m "feat(dashboards): add NATS JetStream monitoring dashboard

Includes consumer lag, message rate, and stream size panels
with configurable time range and auto-refresh.

Closes #8"
```

## Running and Testing

```bash
# Start the full stack
just start

# Check service health
just status

# View logs for a specific service
just logs prometheus

# Test Prometheus scraping
just test-scrape

# Scrape Agamemnon metrics specifically
just scrape-agamemnon

# Reload Prometheus config without restart
just reload-prometheus

# Import Grafana dashboards
just import-dashboards

# Stop the stack
just stop
```

## Pull Request Process

### Before You Start

1. Ensure an issue exists for your work
2. Create a branch from `main` using the naming convention
3. Implement your changes
4. Start the stack and verify your changes work: `just start && just status`

### Creating Your Pull Request

```bash
git push -u origin <branch-name>
gh pr create --title "[Type] Brief description" --body "Closes #<issue-number>"
```

**PR Requirements:**

- PR must be linked to a GitHub issue
- PR title should be clear and descriptive
- Stack must start successfully with changes applied

### Never Push Directly to Main

The `main` branch is protected. All changes must go through pull requests.

## Code Review

### What Reviewers Look For

- **Valid Prometheus YAML** — Are scrape configs and alert rules syntactically correct?
- **Dashboard completeness** — Do Grafana dashboards have meaningful panels and variables?
- **Alert thresholds** — Are alerting thresholds reasonable and well-documented?
- **No hardcoded secrets** — Are credentials in environment variables, not config files?
- **Exporter quality** — Does the Python exporter remain stdlib-only?

### Responding to Review Comments

- Keep responses short (1 line preferred)
- Start with "Fixed -" to indicate resolution

## Markdown Standards

All documentation files must follow these standards:

- Code blocks must have a language tag (`yaml`, `bash`, `python`, `text`, etc.)
- Code blocks must be surrounded by blank lines
- Lists must be surrounded by blank lines
- Headings must be surrounded by blank lines

## Reporting Issues

### Bug Reports

Include: clear title, steps to reproduce, expected vs actual behavior, stack logs.

### Security Issues

**Do not open public issues for security vulnerabilities.**
See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

## Code of Conduct

Please review our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.

---

Thank you for contributing to ProjectArgus!
