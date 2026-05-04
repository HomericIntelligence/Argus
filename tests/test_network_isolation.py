"""
Validate that docker-compose.yml enforces Loki network isolation.
Loki must only be on loki-internal; loki-proxy and promtail bridge both networks;
all other services must NOT be on loki-internal.
"""
import unittest
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DATASOURCES_FILE = REPO_ROOT / "configs" / "grafana" / "datasources.yml"


def load_compose() -> dict:
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


def service_networks(compose: dict, service: str) -> list[str]:
    nets = compose["services"][service].get("networks", [])
    if isinstance(nets, dict):
        return list(nets.keys())
    return list(nets)


class TestLokiInternalNetworkDefined(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_loki_internal_network_exists(self):
        assert "loki-internal" in self.compose["networks"]

    def test_loki_internal_is_internal(self):
        net = self.compose["networks"]["loki-internal"]
        assert net.get("internal") is True

    def test_argus_network_still_exists(self):
        assert "argus" in self.compose["networks"]


class TestLokiServiceNetworks(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_loki_on_loki_internal(self):
        nets = service_networks(self.compose, "loki")
        assert "loki-internal" in nets

    def test_loki_not_on_argus(self):
        nets = service_networks(self.compose, "loki")
        assert "argus" not in nets, "loki must be removed from argus network"


class TestLokiProxyBridgesNetworks(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_loki_proxy_on_argus(self):
        nets = service_networks(self.compose, "loki-proxy")
        assert "argus" in nets

    def test_loki_proxy_on_loki_internal(self):
        nets = service_networks(self.compose, "loki-proxy")
        assert "loki-internal" in nets


class TestPromtailNetworks(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_promtail_on_argus(self):
        nets = service_networks(self.compose, "promtail")
        assert "argus" in nets

    def test_promtail_on_loki_internal(self):
        nets = service_networks(self.compose, "promtail")
        assert "loki-internal" in nets


class TestOtherServicesNotOnLokiInternal(unittest.TestCase):
    """Services with no need to reach Loki directly must not be on loki-internal."""

    ISOLATED_SERVICES = ["prometheus", "grafana", "argus-exporter", "debug-shell"]

    def setUp(self):
        self.compose = load_compose()

    def test_services_not_on_loki_internal(self):
        for svc in self.ISOLATED_SERVICES:
            if svc not in self.compose["services"]:
                continue
            nets = service_networks(self.compose, svc)
            assert "loki-internal" not in nets, (
                f"{svc} must not be on loki-internal (would bypass the proxy)"
            )


class TestGrafanaDatasourcePointsToProxy(unittest.TestCase):
    """Grafana must query Loki via loki-proxy, not directly."""

    def setUp(self):
        with DATASOURCES_FILE.open() as f:
            self.datasources = yaml.safe_load(f)

    def _loki_datasource(self) -> dict:
        for ds in self.datasources["datasources"]:
            if ds.get("type") == "loki":
                return ds
        self.fail("No Loki datasource found in datasources.yml")

    def test_loki_datasource_url_is_proxy(self):
        ds = self._loki_datasource()
        url: str = ds["url"]
        assert "loki-proxy" in url, (
            f"Loki datasource URL must point to loki-proxy, got: {url}"
        )

    def test_loki_datasource_url_not_direct(self):
        ds = self._loki_datasource()
        url: str = ds["url"]
        assert "loki:3100" not in url, (
            f"Loki datasource must not point directly to loki:3100, got: {url}"
        )


if __name__ == "__main__":
    unittest.main()
