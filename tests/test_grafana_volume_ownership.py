"""Tests for the grafana-init volume-ownership init container (issue #332).

The grafana service runs as user "472:472"; Docker named volumes are created
root-owned by default and the local volume driver has no uid/gid option, so a
one-shot ``grafana-init`` container pre-chowns the volume before Grafana
starts. These tests pin the structural invariants of that arrangement.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose_config() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


@pytest.fixture(scope="module")
def grafana_init(compose_config: dict) -> dict:
    services = compose_config.get("services", {})
    assert "grafana-init" in services, (
        "grafana-init service missing from docker-compose.yml"
    )
    return services["grafana-init"]


class TestGrafanaInitService:
    def test_service_exists(self, compose_config: dict) -> None:
        assert "grafana-init" in compose_config.get("services", {})

    def test_mounts_grafana_data_volume(self, grafana_init: dict) -> None:
        volumes = grafana_init.get("volumes", [])
        target = "grafana_data:/var/lib/grafana"
        assert any(
            v.split(":")[0] == "grafana_data" and v.endswith(":/var/lib/grafana")
            for v in volumes
        ), f"grafana-init must mount {target}, got: {volumes}"

    def test_command_chowns_472(self, grafana_init: dict) -> None:
        command_parts = [str(part) for part in grafana_init.get("command", [])]
        command_text = "\n".join(command_parts)
        assert "chown" in command_text, "init command must run chown"
        assert "472:472" in command_text, "chown must target 472:472"

    def test_command_uid_matches_grafana_user(self, compose_config: dict) -> None:
        """UID-drift guard: if grafana's UID changes, the init chown must too."""
        services = compose_config.get("services", {})
        grafana_user = str(services.get("grafana", {}).get("user", ""))
        grafana_uid = grafana_user.split(":")[0]
        init_command = "\n".join(
            str(part) for part in services.get("grafana-init", {}).get("command", [])
        )
        assert f"chown -R {grafana_uid}:" in init_command, (
            f"grafana-init chown UID does not match grafana.user ({grafana_uid}); "
            "update the init container when bumping the grafana image UID"
        )

    def test_grafana_gated_on_init_completion(self, compose_config: dict) -> None:
        depends_on = compose_config.get("services", {}).get("grafana", {}).get(
            "depends_on", {}
        )
        condition = (
            depends_on.get("grafana-init", {}).get("condition")
            if isinstance(depends_on.get("grafana-init"), dict)
            else None
        )
        assert condition == "service_completed_successfully", (
            "grafana must gate on grafana-init via "
            f"condition: service_completed_successfully, got: {depends_on}"
        )

    def test_minimal_privilege_surface(self, grafana_init: dict) -> None:
        assert "ALL" in grafana_init.get("cap_drop", []), "must drop all capabilities"
        cap_add = grafana_init.get("cap_add", [])
        assert set(cap_add) <= {"CHOWN", "FOWNER", "DAC_OVERRIDE"}, (
            f"only ownership caps may be added, got: {cap_add}"
        )
        security_opt = grafana_init.get("security_opt", [])
        assert "no-new-privileges:true" in security_opt
        user = str(grafana_init.get("user", ""))
        assert user == "0:0", "chown requires root; user must be '0:0'"

    def test_no_network_and_read_only_rootfs(self, grafana_init: dict) -> None:
        assert grafana_init.get("network_mode") == "none", (
            "init container touches only a local volume; network must be none"
        )
        assert grafana_init.get("read_only") is True, "rootfs should be read-only"

    def test_never_restarts(self, grafana_init: dict) -> None:
        assert str(grafana_init.get("restart")) == "no", (
            "one-shot container must not restart"
        )

    def test_image_pinned_by_tag(self, grafana_init: dict) -> None:
        image = str(grafana_init.get("image", ""))
        assert image and ":" in image, f"image must be pinned by tag, got: {image}"

    def test_not_in_hardened_services(self) -> None:
        """grafana-init runs as root by design and is intentionally excluded
        from HARDENED_SERVICES in tests/test_container_security.py. This guard
        makes the exclusion explicit so its removal is a deliberate act."""
        from tests.test_container_security import HARDENED_SERVICES

        assert "grafana-init" not in HARDENED_SERVICES
