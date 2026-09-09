"""
Validate that all YAML config files parse correctly and have required top-level keys.
Uses only stdlib: yaml, pathlib, unittest.
"""
import unittest
from pathlib import Path
from typing import Any, ClassVar

import yaml

REPO_ROOT = Path(__file__).parent.parent
CONFIGS_DIR = REPO_ROOT / "configs"


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


class TestPrometheusConfig(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(CONFIGS_DIR / "prometheus.yml")

    def test_parses_without_error(self):
        assert self.config is not None

    def test_has_global_section(self):
        assert "global" in self.config

    def test_global_has_scrape_interval(self):
        assert "scrape_interval" in self.config["global"]

    def test_global_has_evaluation_interval(self):
        assert "evaluation_interval" in self.config["global"]

    def test_has_scrape_configs(self):
        assert "scrape_configs" in self.config

    def test_scrape_configs_is_list(self):
        assert isinstance(self.config["scrape_configs"], list)

    def test_scrape_configs_not_empty(self):
        assert len(self.config["scrape_configs"]) > 0

    def test_each_scrape_config_has_job_name(self):
        for job in self.config["scrape_configs"]:
            assert "job_name" in job, f"Missing job_name in scrape config: {job}"

    def test_has_rule_files(self):
        assert "rule_files" in self.config


class TestLokiConfig(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(CONFIGS_DIR / "loki.yml")

    def test_parses_without_error(self):
        assert self.config is not None

    def test_has_server_section(self):
        assert "server" in self.config

    def test_server_has_http_listen_port(self):
        assert "http_listen_port" in self.config["server"]

    def test_has_schema_config(self):
        assert "schema_config" in self.config

    def test_schema_config_has_configs(self):
        assert "configs" in self.config["schema_config"]

    def test_has_limits_config(self):
        assert "limits_config" in self.config

    def test_limits_config_has_retention_period(self):
        assert "retention_period" in self.config["limits_config"]


class TestPromtailConfig(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(CONFIGS_DIR / "promtail.yml")

    def test_parses_without_error(self):
        assert self.config is not None

    def test_has_server_section(self):
        assert "server" in self.config

    def test_has_clients(self):
        assert "clients" in self.config

    def test_clients_is_list(self):
        assert isinstance(self.config["clients"], list)

    def test_clients_not_empty(self):
        assert len(self.config["clients"]) > 0

    def test_has_scrape_configs(self):
        assert "scrape_configs" in self.config

    def test_scrape_configs_is_list(self):
        assert isinstance(self.config["scrape_configs"], list)

    def test_syslog_job_host_label_uses_env_var(self):
        syslog_job = next(
            (j for j in self.config["scrape_configs"] if j.get("job_name") == "syslog"),
            None,
        )
        assert syslog_job is not None, "syslog scrape job not found"
        labels = syslog_job["static_configs"][0]["labels"]
        assert "host" in labels, "syslog job missing 'host' label"
        assert labels["host"].startswith("${"), (
            "host label must use env var substitution (${HOSTNAME:-...}), "
            f"got hardcoded value: {labels['host']!r}"
        )

    def test_syslog_job_host_label_has_fallback(self):
        syslog_job = next(
            (j for j in self.config["scrape_configs"] if j.get("job_name") == "syslog"),
            None,
        )
        assert syslog_job is not None
        host_val = syslog_job["static_configs"][0]["labels"]["host"]
        assert ":-" in host_val, (
            "host label env var should have a fallback default (e.g. ${HOSTNAME:-hermes}), "
            f"got: {host_val!r}"
        )

    def test_syslog_host_label_is_not_hardcoded(self):
        """host label must use env var substitution, not a hardcoded hostname."""
        syslog_job = next(
            (j for j in self.config["scrape_configs"] if j.get("job_name") == "syslog"),
            None,
        )
        assert syslog_job is not None, "syslog scrape job not found"
        labels = syslog_job["static_configs"][0]["labels"]
        host_val = labels.get("host", "")
        assert host_val.startswith("${"), (
            f"host label must use env var substitution, got hardcoded: {host_val!r}"
        )

    def test_syslog_host_label_uses_env_var(self):
        """host label must reference HOSTNAME via env var expansion syntax."""
        raw = (CONFIGS_DIR / "promtail.yml").read_text()
        assert "HOSTNAME" in raw, "host label must reference ${HOSTNAME} for portability"

    def test_redaction_enabled_jobs_have_secret_patterns(self):
        """All jobs that read host/app logs containing user-supplied data must
        redact the same five secret patterns (bearer, token, key, secret,
        password). Adding a new such job without redaction is a security
        regression — extend REDACTION_ENABLED_JOBS below.

        Issue #190: extended to syslog (host-level logs may contain secrets
        if system services log credentials).
        """
        REDACTION_ENABLED_JOBS = {"syslog", "hermes", "nats"}
        REQUIRED_PATTERNS = ("bearer", "token", "key", "secret", "password")

        jobs_by_name = {
            j["job_name"]: j for j in self.config["scrape_configs"]
        }
        for job_name in REDACTION_ENABLED_JOBS:
            assert job_name in jobs_by_name, f"expected scrape job {job_name!r}"
            stages = jobs_by_name[job_name].get("pipeline_stages")
            assert stages, f"{job_name!r} must have pipeline_stages for redaction"
            joined = " ".join(
                str(s.get("replace", {}).get("expression", "")) for s in stages
            ).lower()
            for pat in REQUIRED_PATTERNS:
                assert pat in joined, (
                    f"{job_name!r} pipeline_stages missing redaction for "
                    f"{pat!r}; got expressions: {joined!r}"
                )

    def _replace_expressions(self, job_name: str) -> list[str]:
        job = next(
            (j for j in self.config["scrape_configs"] if j.get("job_name") == job_name),
            None,
        )
        assert job is not None, f"{job_name} scrape job not found"
        stages = job.get("pipeline_stages", [])
        exprs: list[str] = []
        for stage in stages:
            if isinstance(stage, dict) and "replace" in stage:
                expr = stage["replace"].get("expression")
                if expr:
                    exprs.append(expr)
        return exprs

    def _jobs_with_redaction(self) -> list[str]:
        """Return job_names that already perform credential redaction."""
        jobs: list[str] = []
        for j in self.config["scrape_configs"]:
            stages = j.get("pipeline_stages") or []
            if any(isinstance(s, dict) and "replace" in s for s in stages):
                jobs.append(j["job_name"])
        return jobs

    def test_url_embedded_credentials_redacted_in_all_redaction_pipelines(self):
        """Issue #194: scheme://user:password@host must be redacted in every
        pipeline that already does credential redaction."""
        import re

        jobs = self._jobs_with_redaction()
        assert jobs, "expected at least one job with redaction stages"
        sample_in = "connecting to https://alice:hunter2@db.example.com/foo"
        for job in jobs:
            sample = sample_in
            for expr in self._replace_expressions(job):
                sample = re.sub(expr, "<redacted>", sample, flags=re.IGNORECASE)
            assert "hunter2" not in sample, (
                f"job {job!r}: URL-embedded password leaked: {sample!r}"
            )
            assert "alice" not in sample, (
                f"job {job!r}: URL-embedded username leaked: {sample!r}"
            )

    def test_query_string_api_keys_redacted_in_all_redaction_pipelines(self):
        """Issue #194: ?api_key= / &access_token= etc must be redacted in every
        pipeline that already does credential redaction."""
        import re

        jobs = self._jobs_with_redaction()
        cases = [
            ("GET /v1?api_key=abc123XYZ&name=alice", "abc123XYZ"),
            ("url?access_token=tok_DEADBEEF&next=x", "tok_DEADBEEF"),
            ("/auth?client_secret=shh_SECRET_42 done", "shh_SECRET_42"),
        ]
        for job in jobs:
            exprs = self._replace_expressions(job)
            for line, secret in cases:
                redacted = line
                for expr in exprs:
                    redacted = re.sub(expr, "<redacted>", redacted, flags=re.IGNORECASE)
                assert secret not in redacted, (
                    f"job {job!r}: query-string secret {secret!r} leaked: "
                    f"input={line!r} output={redacted!r}"
                )


class TestGrafanaDatasourcesConfig(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(CONFIGS_DIR / "grafana" / "datasources.yml")

    def test_parses_without_error(self):
        assert self.config is not None

    def test_has_api_version(self):
        assert "apiVersion" in self.config

    def test_has_datasources(self):
        assert "datasources" in self.config

    def test_datasources_is_list(self):
        assert isinstance(self.config["datasources"], list)

    def test_datasources_not_empty(self):
        assert len(self.config["datasources"]) > 0

    def test_each_datasource_has_required_fields(self):
        required_fields = {"name", "type", "uid", "url"}
        for ds in self.config["datasources"]:
            for field in required_fields:
                assert field in ds, f"Datasource missing field '{field}': {ds}"


class TestGrafanaDashboardsConfig(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(CONFIGS_DIR / "grafana" / "dashboards.yml")

    def test_parses_without_error(self):
        assert self.config is not None

    def test_has_api_version(self):
        assert "apiVersion" in self.config

    def test_has_providers(self):
        assert "providers" in self.config

    def test_providers_is_list(self):
        assert isinstance(self.config["providers"], list)

    def test_providers_not_empty(self):
        assert len(self.config["providers"]) > 0

    def test_each_provider_has_required_fields(self):
        required_fields = {"name", "type", "options"}
        for provider in self.config["providers"]:
            for field in required_fields:
                assert field in provider, f"Provider missing field '{field}': {provider}"


class TestDockerComposePromtailEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = load_yaml(REPO_ROOT / "docker-compose.yml")
        self.env = self.compose["services"]["promtail"].get("environment", {})

    def test_promtail_receives_hostname(self) -> None:
        assert "HOSTNAME" in self.env, (
            "promtail must receive HOSTNAME for host-label expansion"
        )

    def test_promtail_receives_host_label_override(self) -> None:
        assert "PROMTAIL_HOST_LABEL" in self.env, (
            "promtail must receive PROMTAIL_HOST_LABEL so the override branch "
            "of ${PROMTAIL_HOST_LABEL:-${HOSTNAME}} is reachable"
        )


class TestDockerComposeNetworkIsolation(unittest.TestCase):
    """Verify that the argus-loki internal network is correctly configured.

    Issue #128: Loki must be isolated to the argus-loki internal network so
    that arbitrary containers on the argus network cannot reach port 3100.
    """

class TestDockerComposePortBindings(unittest.TestCase):
    """Assert no service port is bound to 0.0.0.0 (all-interfaces)."""

    # Only the loopback address is permitted as a host port binding.
    # Rationale: every Argus service exposes either metrics, dashboards, or
    # log endpoints that we deliberately do NOT publish to the LAN — remote
    # access goes via SSH tunnel or Tailscale (see AGENTS.md "Operator
    # Notes"). Binding to 0.0.0.0 (or any non-loopback address) would expose
    # unauthenticated /metrics, /readyz, etc. to anyone on the same network.
    #
    # To add an exception (a service legitimately designed for LAN
    # discovery), open a tracking issue, document the threat model in
    # docker-compose.yml, and extend this set in the same PR — never bypass
    # the test silently.
    ALLOWED_BINDINGS: ClassVar[set[str]] = {"127.0.0.1"}

    def setUp(self) -> None:
        self.compose = load_yaml(REPO_ROOT / "docker-compose.yml")

    def _service_networks(self, service_name: str) -> list[str]:
        nets: Any = self.compose["services"][service_name].get("networks", [])
        if isinstance(nets, dict):
            return list(nets.keys())
        return list(nets)

    def test_loki_internal_network_declared(self) -> None:
        assert "loki-internal" in self.compose["networks"]

    def test_loki_internal_network_is_internal(self) -> None:
        assert self.compose["networks"]["loki-internal"].get("internal") is True

    def test_loki_only_on_loki_internal_network(self) -> None:
        nets = self._service_networks("loki")
        assert "loki-internal" in nets
        assert "argus" not in nets, "loki must not be on the argus network (issue #128)"

    def test_loki_proxy_bridges_both_networks(self) -> None:
        nets = self._service_networks("loki-proxy")
        assert "argus" in nets
        assert "loki-internal" in nets

    def test_promtail_on_loki_internal_network(self) -> None:
        nets = self._service_networks("promtail")
        assert "loki-internal" in nets

    def test_grafana_not_on_loki_internal_network(self) -> None:
        nets = self._service_networks("grafana")
        assert "argus" in nets
        assert "loki-internal" not in nets, "grafana should reach Loki via loki-proxy only"

    def test_debug_shell_not_on_loki_internal_network(self) -> None:
        nets = self._service_networks("debug-shell")
        assert "loki-internal" not in nets, "debug-shell must not access the loki-internal network"

    def test_grafana_depends_on_loki_proxy_not_loki(self) -> None:
        deps: Any = self.compose["services"]["grafana"].get("depends_on", [])
        if isinstance(deps, dict):
            dep_names = list(deps.keys())
        else:
            dep_names = list(deps)
        assert "loki-proxy" in dep_names
        assert "loki" not in dep_names, "grafana should depend on loki-proxy, not loki directly"

    def test_loki_datasource_url_uses_proxy(self) -> None:
        datasources = load_yaml(CONFIGS_DIR / "grafana" / "datasources.yml")["datasources"]
        loki_ds = next(ds for ds in datasources if ds["type"] == "loki")
        assert "loki-proxy" in loki_ds["url"], (
            f"Loki datasource must point to loki-proxy, got: {loki_ds['url']!r}"
        )


class TestDockerComposePorts(unittest.TestCase):
    # Only the loopback address is permitted as a host port binding.
    # Rationale: every Argus service exposes either metrics, dashboards, or
    # log endpoints that we deliberately do NOT publish to the LAN — remote
    # access goes via SSH tunnel or Tailscale (see AGENTS.md "Operator
    # Notes"). Binding to 0.0.0.0 (or any non-loopback address) would expose
    # unauthenticated /metrics, /readyz, etc. to anyone on the same network.
    #
    # To add an exception (a service legitimately designed for LAN
    # discovery), open a tracking issue, document the threat model in
    # docker-compose.yml, and extend this set in the same PR — never bypass
    # the test silently.
    ALLOWED_BINDINGS: ClassVar[set[str]] = {"127.0.0.1"}

    def setUp(self) -> None:
        self.compose = load_yaml(REPO_ROOT / "docker-compose.yml")
        self.services = self.compose["services"]

    def _ports(self, service: str) -> list[str]:
        return self.services[service].get("ports", [])

    def test_prometheus_port_is_loopback_bound(self) -> None:
        ports = self._ports("prometheus")
        assert any(str(p).startswith("127.0.0.1:") and ":9090" in str(p) for p in ports), (
            f"Prometheus must bind to 127.0.0.1:*:9090, got: {ports}"
        )

    def test_prometheus_port_not_open_bound(self) -> None:
        ports = self._ports("prometheus")
        assert not any(str(p) == "9090:9090" for p in ports), (
            f"Prometheus must not bind to 0.0.0.0:9090, got: {ports}"
        )

    def test_grafana_port_is_loopback_bound(self) -> None:
        ports = self._ports("grafana")
        assert any(str(p).startswith("127.0.0.1:") for p in ports), (
            f"Grafana must bind to 127.0.0.1, got: {ports}"
        )

    def test_grafana_port_not_open_bound(self) -> None:
        ports = self._ports("grafana")
        assert not any(str(p) == "3000:3000" or str(p) == "3001:3000" for p in ports), (
            f"Grafana must not bind to 0.0.0.0, got: {ports}"
        )

    def test_exporter_port_is_loopback_bound(self) -> None:
        ports = self._ports("argus-exporter")
        assert any(str(p).startswith("127.0.0.1:") and ":9100" in str(p) for p in ports), (
            f"argus-exporter must bind to 127.0.0.1:*:9100, got: {ports}"
        )

    def test_grafana_anonymous_access_disabled(self) -> None:
        env = self.services["grafana"].get("environment", {})
        assert env.get("GF_AUTH_ANONYMOUS_ENABLED") == "false", (
            f"GF_AUTH_ANONYMOUS_ENABLED must be 'false', got: {env.get('GF_AUTH_ANONYMOUS_ENABLED')}"
        )

    def test_no_wildcard_port_bindings(self) -> None:
        services = self.compose.get("services", {})
        for svc_name, svc in services.items():
            for port_entry in svc.get("ports", []):
                port_str = str(port_entry)
                parts = port_str.split(":")
                if len(parts) == 1:
                    self.fail(
                        f"Service '{svc_name}' has bare port binding '{port_str}' "
                        f"(implicit 0.0.0.0). Use '127.0.0.1:{port_str}:{port_str}' instead."
                    )
                elif len(parts) == 2:
                    self.fail(
                        f"Service '{svc_name}' binds port '{port_str}' on 0.0.0.0. "
                        f"Use '127.0.0.1:{parts[0]}:{parts[1]}' instead."
                    )
                else:
                    bind_ip = parts[0]
                    self.assertIn(
                        bind_ip,
                        self.ALLOWED_BINDINGS,
                        f"Service '{svc_name}' port '{port_str}' binds to '{bind_ip}', "
                        f"not in allowed set {self.ALLOWED_BINDINGS}.",
                    )


if __name__ == "__main__":
    unittest.main()
