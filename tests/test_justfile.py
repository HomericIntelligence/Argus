"""
Tests for the justfile: hardcoded-credential guards plus the rotate-htpasswd
alias (issue #227).

Credential guards verify the justfile keeps no hardcoded Grafana credentials.
Alias tests verify that `rotate-htpasswd` is defined as an alias for
`gen-htpasswd`, that its target recipe still exists, and that it is
discoverable via `just --list` when the just binary is available.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
JUSTFILE = REPO_ROOT / "justfile"


def _justfile_content() -> str:
    return JUSTFILE.read_text()


# ---------------------------------------------------------------------------
# .env override wiring (issue #410)
# ---------------------------------------------------------------------------


def test_agamemnon_url_overridable() -> None:
    """AGAMEMNON_URL must accept .env overrides via env_var_or_default."""
    assert 'AGAMEMNON_URL := env_var_or_default("AGAMEMNON_URL"' in _justfile_content(), (
        "AGAMEMNON_URL is hardcoded; must use env_var_or_default to honor .env"
    )


def test_grafana_port_overridable() -> None:
    """GRAFANA_PORT must accept .env overrides via env_var_or_default."""
    assert 'GRAFANA_PORT := env_var_or_default("GRAFANA_PORT"' in _justfile_content(), (
        "GRAFANA_PORT is hardcoded; must use env_var_or_default to honor .env"
    )


def test_env_example_has_no_duplicate_keys() -> None:
    """`.env.example` must define each key at most once."""
    keys: list[str] = []
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0])
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, f"Duplicate keys in .env.example: {duplicates}"


# ---------------------------------------------------------------------------
# Hardcoded-credential guards (pre-existing coverage — do not drop)
# ---------------------------------------------------------------------------


def test_no_admin_colon_admin() -> None:
    """admin:admin must not appear anywhere in the justfile."""
    content = _justfile_content()
    assert "admin:admin" not in content, (
        "Hardcoded credential 'admin:admin' found in justfile"
    )


def test_no_grafana_auth_variable() -> None:
    """The GRAFANA_AUTH variable definition must not exist in the justfile."""
    content = _justfile_content()
    assert "GRAFANA_AUTH" not in content, "Variable 'GRAFANA_AUTH' still present in justfile"


def test_dotenv_load_enabled() -> None:
    """set dotenv-load must be present so .env is read at recipe time."""
    content = _justfile_content()
    assert "set dotenv-load" in content, "'set dotenv-load' not found in justfile"


def test_import_dashboards_uses_gf_admin_password() -> None:
    """import-dashboards recipe must reference GF_ADMIN_PASSWORD from env."""
    content = _justfile_content()
    assert "GF_ADMIN_PASSWORD" in content, (
        "import-dashboards recipe does not reference GF_ADMIN_PASSWORD"
    )


def test_import_dashboards_exports_container_cmd() -> None:
    """import-dashboards must export CONTAINER_CMD like every other script recipe.

    Issue #354: scripts invoked by recipes consistently receive
    ``CONTAINER_CMD`` so container-based logic can pick docker vs podman.
    import-dashboards.sh only uses curl today, but the env var must be
    present if it ever grows a container call.
    """
    match = re.search(
        r"^import-dashboards:(.*?)(?=^\S|\Z)", _justfile_content(), re.MULTILINE | re.DOTALL
    )
    assert match, "import-dashboards recipe not found in justfile"
    assert "CONTAINER_CMD={{container_cmd}}" in match.group(1), (
        "import-dashboards recipe must export CONTAINER_CMD={{container_cmd}} "
        "so scripts inherit the resolved container runtime"
    )


def test_script_recipes_export_container_cmd() -> None:
    """Every recipe that runs a scripts/*.sh wrapper must pass CONTAINER_CMD."""
    content = _justfile_content()
    assert content.count("CONTAINER_CMD={{container_cmd}}") >= 3, (
        "backup, restore, and import-dashboards recipes should all export "
        "CONTAINER_CMD={{container_cmd}}"
    )


def test_no_cut_d_colon_credential_extraction() -> None:
    """Credential extraction via 'cut -d: -f2' must be gone from the justfile."""
    content = _justfile_content()
    assert "cut -d:" not in content, (
        "Credential extraction via 'cut -d:' still present in justfile"
    )


# ---------------------------------------------------------------------------
# wget flag portability (issue #198)
# ---------------------------------------------------------------------------


def test_no_combined_qO_flag_in_justfile() -> None:
    """The combined `-qO-` flag is not portable across GNU and BusyBox wget."""
    content = _justfile_content()
    assert "-qO-" not in content, (
        "Non-portable combined '-qO-' wget flag found in justfile"
    )


def test_reload_prometheus_uses_verified_https_post() -> None:
    """reload-prometheus must POST to /-/reload over the CA-verified endpoint."""
    content = _justfile_content()
    assert "-X POST" in content, (
        "reload-prometheus recipe must use an explicit HTTP POST"
    )
    assert "https://localhost:9090/-/reload" in content, (
        "reload-prometheus recipe missing the /-/reload endpoint"
    )
    assert "--cacert certs/ca.crt" in content, (
        "reload-prometheus recipe must verify the Argus CA certificate"
    )


# ---------------------------------------------------------------------------
# Prometheus HTTPS probes (issue #206)
# ---------------------------------------------------------------------------


def test_no_plaintext_prometheus_probe_in_justfile() -> None:
    """Prometheus serves HTTPS since the web config split, so no http probe may survive."""
    content = _justfile_content()
    assert "http://localhost:9090" not in content, (
        "the justfile still probes Prometheus over plaintext http; it serves "
        "HTTPS since configs/prometheus-web.yml landed (#206)"
    )


def test_test_scrape_uses_https() -> None:
    """test-scrape must query the Prometheus HTTPS endpoint."""
    content = _justfile_content()
    assert "https://localhost:9090/api/v1/query?query=up" in content, (
        "test-scrape must query Prometheus over https"
    )


def test_prometheus_probes_verify_argus_ca() -> None:
    """Prometheus operator probes must verify the Argus CA certificate."""
    content = _justfile_content()
    assert content.count("--cacert certs/ca.crt") >= 2, (
        "reload-prometheus and test-scrape must both pass the Argus CA"
    )
    assert "--no-check-certificate" not in content, (
        "Prometheus probes must not bypass certificate verification"
    )


# ---------------------------------------------------------------------------
# rotate-htpasswd alias (issue #227)
# ---------------------------------------------------------------------------


def test_rotate_htpasswd_alias_defined() -> None:
    """The alias statement must exist exactly as written."""
    content = _justfile_content()
    assert re.search(r"(?m)^alias rotate-htpasswd := gen-htpasswd$", content)


def test_alias_target_recipe_exists() -> None:
    """The alias target recipe gen-htpasswd must still be defined."""
    content = _justfile_content()
    assert re.search(r"(?m)^gen-htpasswd:", content)


def test_gen_htpasswd_docstring_mentions_rotation() -> None:
    """The gen-htpasswd doc comment must describe both generation and rotation."""
    content = _justfile_content()
    assert "Generate or rotate configs/nginx/htpasswd" in content


@pytest.mark.skipif(shutil.which("just") is None, reason="just binary not on PATH")
def test_alias_listed_by_just_list() -> None:
    """`just --list` must surface rotate-htpasswd for discoverability."""
    result = subprocess.run(
        ["just", "--list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "rotate-htpasswd" in result.stdout


@pytest.mark.skipif(shutil.which("just") is None, reason="just binary not on PATH")
def test_alias_dry_run_dispatches_to_gen_htpasswd() -> None:
    """Dry-running the alias must print the gen-htpasswd recipe body."""
    result = subprocess.run(
        ["just", "-n", "rotate-htpasswd"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # `just -n` prints shebang recipe bodies to stderr
    combined = result.stdout + result.stderr
    assert "htpasswd -nbB loki" in combined


# ---------------------------------------------------------------------------
# .env presence guard (issue #214)
# ---------------------------------------------------------------------------


def _check_env_body() -> str:
    """Return the body of the check-env recipe (up to the next comment block)."""
    content = _justfile_content()
    start = content.index("\ncheck-env:\n")
    end = content.find("\n# ", start)
    return content[start:] if end == -1 else content[start:end]


def test_check_env_recipe_present() -> None:
    """The check-env guard recipe must exist in the justfile."""
    assert "\ncheck-env:\n" in _justfile_content(), "check-env recipe not found in justfile"


def test_start_depends_on_check_env() -> None:
    """start must declare the check-env dependency so the guard runs first."""
    assert "start: check-env" in _justfile_content(), (
        "start recipe does not depend on check-env"
    )


def test_restart_depends_on_check_env() -> None:
    """restart must declare the check-env dependency so the guard runs first."""
    assert "restart: check-env" in _justfile_content(), (
        "restart recipe does not depend on check-env"
    )


def test_check_env_exits_nonzero() -> None:
    """check-env must exit non-zero when .env is missing."""
    assert "exit 1" in _check_env_body(), "check-env recipe does not exit non-zero"


def test_check_env_remediation_mentions_env_example() -> None:
    """check-env's remediation hint must point at .env.example."""
    assert ".env.example" in _check_env_body(), (
        "check-env remediation hint does not mention .env.example"
    )
