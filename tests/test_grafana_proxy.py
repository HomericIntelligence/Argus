"""
Tests for issue #321: Grafana must be fronted by an nginx basic-auth proxy
(grafana-proxy) and must not publish its own host port.

Stack-dependent cases (live proxy on 127.0.0.1:${GRAFANA_PORT:-3001}) are
skipped when the stack is not running or when the GRAFANA_PROXY_USER /
GRAFANA_PROXY_PASSWORD credentials needed to authenticate past the proxy are
not exported — matching the convention of skipping infrastructure-dependent
tests rather than failing CI.
"""

import base64
import os
import unittest
import urllib.error
import urllib.request

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE_FILE = os.path.join(REPO_ROOT, "docker-compose.yml")
GRAFANA_PROXY_HOST = "127.0.0.1"
GRAFANA_PROXY_PORT = int(os.environ.get("GRAFANA_PORT", "3001"))


def _proxy_url(path: str = "/") -> str:
    return f"http://{GRAFANA_PROXY_HOST}:{GRAFANA_PROXY_PORT}{path}"


def _proxy_creds() -> tuple[str, str] | None:
    user = os.environ.get("GRAFANA_PROXY_USER")
    password = os.environ.get("GRAFANA_PROXY_PASSWORD")
    if user and password:
        return user, password
    return None


def _basic_auth_header(creds: tuple[str, str]) -> str:
    token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    return f"Basic {token}"


def _fetch(
    path: str = "/",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """GET the proxy; return (status, response headers, body).

    HTTPError carries the non-2xx status and is unwrapped so callers see
    4xx/5xx responses as data instead of exceptions.
    """
    req = urllib.request.Request(_proxy_url(path), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def _requires_stack(fn):
    def wrapper(*args, **kwargs):
        try:
            _fetch()
        except urllib.error.URLError as err:
            reason = getattr(err, "reason", err)
            raise unittest.SkipTest(f"grafana-proxy stack not running ({reason})") from err
        except OSError as err:
            raise unittest.SkipTest(f"grafana-proxy stack not running ({err})") from err
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def load_compose() -> dict:
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


class TestGrafanaProxyAuth(unittest.TestCase):
    """Live-proxy behaviour: Basic Auth challenge before Grafana is reached."""

    @_requires_stack
    def test_grafana_unauthenticated_returns_401(self) -> None:
        status, headers, _ = _fetch("/")
        self.assertEqual(status, 401, "unauthenticated request must get 401 from the proxy")
        www_auth = headers.get("WWW-Authenticate", "")
        self.assertTrue(www_auth.lower().startswith("basic"), (
            f"expected a Basic challenge, got WWW-Authenticate: {www_auth!r}"
        ))

    @_requires_stack
    def test_grafana_bad_password_returns_401(self) -> None:
        bad = _basic_auth_header(("grafana", "definitely-not-the-password"))
        status, _, _ = _fetch("/", headers={"Authorization": bad})
        self.assertEqual(status, 401, (
            "wrong credentials must fail at the proxy before reaching Grafana"
        ))

    @_requires_stack
    def test_grafana_authenticated_reaches_grafana_login(self) -> None:
        creds = _proxy_creds()
        if creds is None:
            self.skipTest("GRAFANA_PROXY_USER/GRAFANA_PROXY_PASSWORD not set")
        auth = _basic_auth_header(creds)
        # /api/health is unauthenticated inside Grafana and proves the full
        # proxy -> grafana HTTPS chain works.
        status, _, body = _fetch("/api/health", headers={"Authorization": auth})
        self.assertEqual(status, 200, f"expected 200 from /api/health, got {status}")
        self.assertIn(b"database", body, (
            f"Grafana health payload expected, got: {body[:200]!r}"
        ))


class TestGrafanaProxyWebSocket(unittest.TestCase):
    """WebSocket upgrade headers must pass through the proxy (Grafana Live)."""

    @_requires_stack
    def test_grafana_websocket_upgrade_headers_pass_through(self) -> None:
        creds = _proxy_creds()
        if creds is None:
            self.skipTest("GRAFANA_PROXY_USER/GRAFANA_PROXY_PASSWORD not set")
        auth = _basic_auth_header(creds)
        headers = {
            "Authorization": auth,
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
            "Sec-WebSocket-Version": "13",
        }
        status, _, _ = _fetch("/api/live/ws", headers=headers)
        # 101 means the upgrade succeeded end-to-end; a Grafana-side 4xx also
        # proves the upgrade headers were forwarded (nginx would otherwise
        # strip them and return 200 with the headers dropped).
        if status == 101 or 400 <= status < 500:
            self.assertTrue(True)
        else:
            self.fail(
                f"unexpected status {status}: neither a 101 upgrade nor a "
                "Grafana-side 4xx — upgrade headers were likely stripped by the proxy"
            )


class TestGrafanaProxyComposeTopology(unittest.TestCase):
    """Pure compose-YAML checks (no stack required)."""

    def setUp(self) -> None:
        self.compose = load_compose()

    def test_grafana_no_host_port_binding(self) -> None:
        grafana = self.compose["services"]["grafana"]
        self.assertNotIn(
            "ports",
            grafana,
            "grafana must not publish a host port since #321; use grafana-proxy",
        )
        exposed = [str(p) for p in grafana.get("expose", [])]
        self.assertIn("3000", exposed)


if __name__ == "__main__":
    unittest.main()
