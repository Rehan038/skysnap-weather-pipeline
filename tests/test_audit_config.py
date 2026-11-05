"""Audit tests for exposure and configuration (compose, CI, docker context).

Each xfail(strict=True) documents a known finding; remove the marker when fixed.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def _published(service: str) -> list[str]:
    return [str(p) for p in COMPOSE["services"][service].get("ports", [])]


def _is_loopback(spec: str) -> bool:
    return spec.startswith(("127.0.0.1:", "[::1]:"))


@pytest.mark.xfail(strict=True, reason="FINDING E-01")
def test_airflow_ui_is_bound_to_loopback():
    """Airflow runs with ALL_ADMINS (no login): it must not listen on the LAN."""
    ports = _published("airflow-apiserver")
    assert ports and all(_is_loopback(p) for p in ports), ports


@pytest.mark.xfail(strict=True, reason="FINDING E-02")
def test_mysql_is_bound_to_loopback():
    ports = _published("warehouse")
    assert ports and all(_is_loopback(p) for p in ports), ports


@pytest.mark.xfail(strict=True, reason="FINDING E-03")
def test_streamlit_is_bound_to_loopback():
    """The app can spend Gemini quota and read warehouse data."""
    ports = _published("ask")
    assert ports and all(_is_loopback(p) for p in ports), ports


def _env(service: str) -> dict:
    env = COMPOSE["services"][service].get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return env


@pytest.mark.xfail(strict=True, reason="FINDING E-04")
@pytest.mark.parametrize(
    ("service", "key"),
    [
        ("warehouse", "MYSQL_PASSWORD"),
        ("warehouse", "MYSQL_ROOT_PASSWORD"),
        ("postgres", "POSTGRES_PASSWORD"),
    ],
)
def test_service_passwords_are_not_literal_defaults(service, key):
    """Passwords must come from .env with no usable built-in default."""
    value = str(_env(service)[key])
    assert value.startswith("${") and ":-" not in value, "literal or defaulted secret"


@pytest.mark.xfail(strict=True, reason="FINDING E-05")
def test_ci_declares_least_privilege_token_permissions():
    assert CI.get("permissions") == {"contents": "read"}


def test_ci_actions_are_pinned_to_at_least_a_major_version():
    for job in CI["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses")
            if uses:
                assert "@" in uses and not uses.endswith("@main") and not uses.endswith("@master"), uses


def test_ci_uses_no_repository_secrets():
    assert "secrets." not in (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="FINDING E-06")
def test_dockerignore_excludes_backups_and_dumps():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "backup_*.sql" in lines


def test_dockerignore_excludes_env_and_vcs():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for needed in (".env", ".venv", ".git", ".cache"):
        assert needed in lines


def test_env_file_is_gitignored_and_never_committed():
    import subprocess

    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in gi
    out = subprocess.run(
        ["git", "log", "--all", "--oneline", "--", ".env"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip() == ""
