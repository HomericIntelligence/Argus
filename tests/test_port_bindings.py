"""
Static assertion that every host-exposed port binding in docker-compose.yml
is loopback-only (127.0.0.1 or ::1).

This is the canonical, stack-independent check for issue #327 (follow-up to
#126): it prevents a new service from silently leaking metric/log endpoints
onto 0.0.0.0. The runtime counterpart is `just check-ports`
(scripts/check-ports.sh), which inspects the *running* stack.

Currently covers the 7 host port bindings at docker-compose.yml lines:
prometheus (:22), loki-proxy (:121), alertmanager (:179), grafana (:204),
argus-exporter (:260), argus-dashboard (:303), jetstream-consumer (:363).
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})
EXPECTED_BINDING_COUNT: int = 7

# Matches ${VAR} and ${VAR:-default} compose interpolation placeholders.
_ENV_PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"\$\{[^}]*\}")


def load_compose() -> dict:
    with COMPOSE_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def iter_port_entries(compose: dict) -> Iterator[tuple[str, str | dict]]:
    """Yield (service_name, raw_entry) for every services.<svc>.ports element.

    Services without a ``ports:`` key are skipped.
    """
    services: dict = compose.get("services", {})
    for name, svc in sorted(services.items()):
        ports = (svc or {}).get("ports")
        if not ports:
            continue
        for entry in ports:
            yield name, entry


def extract_host_ip(entry: Any) -> str | None:
    """Return the host_ip of a compose port entry, or None when unbound.

    Short-form strings are ``HOST_IP:HOST_PORT:CONTAINER_PORT`` or
    ``HOST_PORT:CONTAINER_PORT``; env-interpolated forms such as
    ``127.0.0.1:${GRAFANA_PORT:-3001}:3000`` resolve cleanly because
    ``${VAR:-default}`` placeholders (which may themselves contain colons)
    are replaced before segmenting. Long-form dicts use the ``host_ip`` key.
    A missing/empty host IP means Docker's default ``0.0.0.0`` binding —
    callers must treat that as a violation.
    """
    if isinstance(entry, dict):
        return entry.get("host_ip") or None
    if not isinstance(entry, str):
        return None
    # Neutralize ${VAR} / ${VAR:-default} placeholders first: the default
    # value can contain a ':' which would otherwise skew segmentation.
    normalized: str = _ENV_PLACEHOLDER_RE.sub("VAR", entry)
    parts: list[str] = normalized.split(":")
    if len(parts) < 3:
        # "HOST_PORT:CONTAINER_PORT" or bare "HOST_PORT": the HOST_IP part
        # was omitted and Docker binds to 0.0.0.0 by default.
        return None
    # Joining all but the last two segments also handles IPv6 hosts such as
    # "::1:3101:80", whose leading empty segments reconstruct "::1".
    return ":".join(parts[:-2])


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize port-binding tests over every entry in docker-compose.yml."""
    if "port_binding" not in metafunc.fixturenames:
        return
    compose = load_compose()
    cases = sorted(iter_port_entries(compose), key=lambda c: (c[0], str(c[1])))
    metafunc.parametrize(
        ("service", "port_binding"),
        [pytest.param(svc, entry, id=f"{svc}-{entry}") for svc, entry in cases],
    )


def test_binding_count_matches_expected() -> None:
    """The binding count must match the audited inventory from issue #327."""
    compose = load_compose()
    entries = list(iter_port_entries(compose))
    assert len(entries) == EXPECTED_BINDING_COUNT, (
        f"Expected {EXPECTED_BINDING_COUNT} host port bindings in "
        f"docker-compose.yml, found {len(entries)}. If a binding was "
        f"intentionally added, verify it is loopback-only and update "
        f"EXPECTED_BINDING_COUNT."
    )


def test_every_port_binding_is_loopback_only(service: str, port_binding: Any) -> None:
    """Every host port binding must be explicitly bound to 127.0.0.1 or ::1."""
    host_ip = extract_host_ip(port_binding)
    assert host_ip in LOOPBACK_HOSTS, (
        f"service '{service}' exposes a non-loopback port binding: "
        f"{port_binding!r} (host_ip={host_ip!r}). Bind it to 127.0.0.1 "
        f"explicitly."
    )


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("127.0.0.1:${GRAFANA_PORT:-3001}:3000", "127.0.0.1"),
        ("127.0.0.1:${EXPORTER_PORT:-9100}:9100", "127.0.0.1"),
        ("127.0.0.1:9090:9090", "127.0.0.1"),
        ("::1:3101:80", "::1"),
        ("9090:9090", None),
        ("9090", None),
        ({"published": "9090", "target": 9090}, None),
        ({"host_ip": "0.0.0.0", "published": "9090", "target": 9090}, "0.0.0.0"),
    ],
)
def test_extract_host_ip(entry: Any, expected: str | None) -> None:
    """Unit-test the extractor for short-form, long-form, and unbound inputs."""
    assert extract_host_ip(entry) == expected
