"""
Guard against AGENTS.md (the doc CLAUDE.md points at) drifting from the
actual configuration files. Issue #235: version references, scrape-target
tables, and retention/interval claims must match compose and configs.

Uses only stdlib plus yaml/pexpect-free parsing: yaml, pathlib, unittest.
"""
import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
AGENTS_MD = (REPO_ROOT / "AGENTS.md").read_text()


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def section(text: str, heading: str) -> str:
    """Return the body of a `## <heading>` section (up to the next `## `)."""
    parts = text.split(heading)
    if len(parts) < 2:
        raise AssertionError(f"section {heading!r} not found in document")
    return parts[1].split("\n## ")[0]


class TestStackComponentsTable(unittest.TestCase):
    """Every pinned image in docker-compose.yml must be documented."""

    def setUp(self):
        self.compose = load_yaml(REPO_ROOT / "docker-compose.yml")
        self.table = section(AGENTS_MD, "## Stack Components")

    def _documented_rows(self) -> dict[str, str]:
        """Map Stack Components rows: lowercase service name -> image cell."""
        rows: dict[str, str] = {}
        for line in self.table.splitlines():
            if not line.startswith("|") or line.startswith("|-"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or cells[0] == "Service":
                continue
            rows[cells[0].lower()] = cells[1]
        return rows

    def test_no_latest_tags_documented(self):
        self.assertNotIn(":latest", AGENTS_MD)

    def test_every_compose_service_is_documented_with_matching_image(self):
        for service, spec in self.compose["services"].items():
            rows = self._documented_rows()
            self.assertIn(
                service,
                rows,
                f"docker-compose service '{service}' has no row in the "
                f"AGENTS.md Stack Components table",
            )
            image = spec.get("image", "")
            # Locally built (${...} interpolated or build:) images have no
            # registry tag to pin against; everything else must match exactly.
            if image and "${" not in image and not spec.get("build"):
                self.assertIn(
                    image,
                    rows[service],
                    f"AGENTS.md documents '{rows[service]}' for service "
                    f"'{service}' but docker-compose.yml pins '{image}'",
                )

    def test_documented_registry_tags_exist_in_compose(self):
        """Reverse check: a pinned repo:tag in AGENTS.md must still exist in
        docker-compose.yml (catches docs referencing removed/downgraded images)."""
        compose_images = {
            spec.get("image", "")
            for spec in self.compose["services"].values()
        }
        for service, image_cell in self._documented_rows().items():
            match = re.match(r"^([A-Za-z0-9_./-]+:[A-Za-z0-9_.-]+)", image_cell)
            if not match:
                continue  # locally built / descriptive row, nothing pinned
            self.assertIn(
                match.group(1),
                compose_images,
                f"AGENTS.md Stack Components documents '{match.group(1)}' for "
                f"'{service}' but docker-compose.yml does not use that image",
            )


class TestScrapeTargetsSection(unittest.TestCase):
    """Scrape jobs documented in AGENTS.md must exist in prometheus.yml."""

    def setUp(self):
        self.prom = load_yaml(REPO_ROOT / "configs" / "prometheus.yml")
        self.section_text = section(AGENTS_MD, "## Scrape Targets")

    def test_documented_jobs_exist_in_prometheus_config(self):
        real_jobs = {job["job_name"] for job in self.prom["scrape_configs"]}
        table_body = [
            line
            for line in self.section_text.splitlines()
            if line.startswith("|") and not line.startswith("|--")
        ]
        documented_jobs = {line.split("|")[1].strip() for line in table_body}
        documented_jobs.discard("Job")
        self.assertTrue(documented_jobs, "no scrape jobs parsed from AGENTS.md")
        for job in documented_jobs:
            self.assertIn(
                job,
                real_jobs,
                f"AGENTS.md documents scrape job '{job}' that does not exist "
                f"in configs/prometheus.yml (actual jobs: {sorted(real_jobs)})",
            )

    def test_every_prometheus_job_is_documented(self):
        real_jobs = {job["job_name"] for job in self.prom["scrape_configs"]}
        for job in real_jobs:
            self.assertIn(
                f"| {job} ",
                self.section_text,
                f"scrape job '{job}' exists in configs/prometheus.yml but is "
                f"not documented in the AGENTS.md Scrape Targets table",
            )

    def test_no_direct_upstream_scrape_targets_claimed(self):
        """Agamemnon/Nestor/NATS are polled by the exporter, never scraped by
        Prometheus directly (issue #235: stale localhost:8222 claim)."""
        ports = {"8080", "8081", "8222"}
        cells = re.findall(r"\|\s*`?[a-zA-Z0-9_.-]+:(\d+)`?\s*\|", self.section_text)
        offenders = [p for p in cells if p in ports]
        self.assertEqual(
            offenders,
            [],
            f"AGENTS.md Scrape Targets table lists upstream port(s) {offenders}; "
            f"Prometheus must not scrape Agamemnon/Nestor/NATS directly",
        )


class TestKeyPrinciplesMatchConfigs(unittest.TestCase):
    def test_scrape_interval_claim_matches_prometheus_config(self):
        prom = load_yaml(REPO_ROOT / "configs" / "prometheus.yml")
        interval = prom["global"]["scrape_interval"]
        self.assertIn(
            f"scrape interval is {interval}",
            AGENTS_MD,
            f"AGENTS.md Key Principles claims a different scrape interval "
            f"than configs/prometheus.yml ({interval})",
        )

    def test_retention_claim_matches_loki_config(self):
        loki = load_yaml(REPO_ROOT / "configs" / "loki.yml")
        retention = loki["limits_config"]["retention_period"]
        self.assertIn(
            f"({retention})",
            AGENTS_MD,
            f"AGENTS.md Key Principles claims a different Loki retention than "
            f"configs/loki.yml ({retention})",
        )


if __name__ == "__main__":
    unittest.main()
