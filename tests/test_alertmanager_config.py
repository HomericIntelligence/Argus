"""Tests for Alertmanager configuration and related integration files."""

from pathlib import Path
import yaml
import pytest

REPO_ROOT = Path(__file__).parent.parent
ALERTMANAGER_CONFIG = REPO_ROOT / "configs" / "alertmanager.yml"
PROMETHEUS_CONFIG = REPO_ROOT / "configs" / "prometheus.yml"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def alertmanager_config() -> dict:
    return yaml.safe_load(ALERTMANAGER_CONFIG.read_text())


@pytest.fixture(scope="module")
def prometheus_config() -> dict:
    return yaml.safe_load(PROMETHEUS_CONFIG.read_text())


@pytest.fixture(scope="module")
def compose_config() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


class TestAlertmanagerConfigExists:
    def test_config_file_present(self) -> None:
        assert ALERTMANAGER_CONFIG.exists(), "configs/alertmanager.yml must exist"

    def test_config_is_valid_yaml(self) -> None:
        content = ALERTMANAGER_CONFIG.read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)


class TestAlertmanagerConfigStructure:
    def test_has_global_section(self, alertmanager_config: dict) -> None:
        assert "global" in alertmanager_config

    def test_global_resolve_timeout_set(self, alertmanager_config: dict) -> None:
        assert "resolve_timeout" in alertmanager_config["global"]

    def test_has_route_section(self, alertmanager_config: dict) -> None:
        assert "route" in alertmanager_config

    def test_route_has_receiver(self, alertmanager_config: dict) -> None:
        assert "receiver" in alertmanager_config["route"]

    def test_route_has_group_by(self, alertmanager_config: dict) -> None:
        assert "group_by" in alertmanager_config["route"]

    def test_route_has_group_wait(self, alertmanager_config: dict) -> None:
        assert "group_wait" in alertmanager_config["route"]

    def test_route_has_repeat_interval(self, alertmanager_config: dict) -> None:
        assert "repeat_interval" in alertmanager_config["route"]

    def test_has_receivers_section(self, alertmanager_config: dict) -> None:
        assert "receivers" in alertmanager_config
        assert isinstance(alertmanager_config["receivers"], list)
        assert len(alertmanager_config["receivers"]) >= 1

    def test_default_receiver_exists_in_receivers(self, alertmanager_config: dict) -> None:
        default_receiver = alertmanager_config["route"]["receiver"]
        receiver_names = [r["name"] for r in alertmanager_config["receivers"]]
        assert default_receiver in receiver_names, (
            f"Default receiver '{default_receiver}' must be defined in receivers"
        )

    @pytest.mark.parametrize("field", ["group_by", "group_wait", "group_interval", "repeat_interval", "receiver"])
    def test_route_required_fields(self, alertmanager_config: dict, field: str) -> None:
        assert field in alertmanager_config["route"], f"route.{field} must be present"


class TestPrometheusAlertingBlock:
    def test_alerting_block_present(self, prometheus_config: dict) -> None:
        assert "alerting" in prometheus_config, (
            "prometheus.yml must contain an 'alerting' block"
        )

    def test_alertmanagers_configured(self, prometheus_config: dict) -> None:
        alerting = prometheus_config["alerting"]
        assert "alertmanagers" in alerting
        assert len(alerting["alertmanagers"]) >= 1

    def test_alertmanager_target_is_docker_hostname(self, prometheus_config: dict) -> None:
        targets = []
        for am in prometheus_config["alerting"]["alertmanagers"]:
            for sc in am.get("static_configs", []):
                targets.extend(sc.get("targets", []))
        assert any("alertmanager" in t for t in targets), (
            "Alertmanager target must use Docker hostname 'alertmanager', not 'localhost'"
        )

    def test_alertmanager_port_is_9093(self, prometheus_config: dict) -> None:
        targets = []
        for am in prometheus_config["alerting"]["alertmanagers"]:
            for sc in am.get("static_configs", []):
                targets.extend(sc.get("targets", []))
        assert any(":9093" in t for t in targets), (
            "Alertmanager target must specify port 9093"
        )


class TestDockerComposeAlertmanager:
    def test_alertmanager_service_defined(self, compose_config: dict) -> None:
        assert "alertmanager" in compose_config["services"], (
            "docker-compose.yml must define an 'alertmanager' service"
        )

    def test_alertmanager_image(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        assert svc["image"] == "prom/alertmanager:latest"

    def test_alertmanager_port_exposed(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        ports = svc.get("ports", [])
        assert any("9093" in str(p) for p in ports)

    def test_alertmanager_config_mounted_readonly(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        volumes = svc.get("volumes", [])
        assert any("alertmanager.yml" in str(v) and ":ro" in str(v) for v in volumes), (
            "alertmanager.yml must be mounted read-only (:ro)"
        )

    def test_alertmanager_on_argus_network(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        networks = svc.get("networks", [])
        assert "argus" in networks

    def test_alertmanager_depends_on_prometheus(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        depends = svc.get("depends_on", [])
        assert "prometheus" in depends

    def test_alertmanager_data_volume_declared(self, compose_config: dict) -> None:
        top_level_volumes = compose_config.get("volumes", {})
        assert "alertmanager_data" in top_level_volumes, (
            "alertmanager_data volume must be declared at top level"
        )

    def test_restart_policy(self, compose_config: dict) -> None:
        svc = compose_config["services"]["alertmanager"]
        assert svc.get("restart") == "unless-stopped"
