"""Exercise the actual Makefile target without Docker or package installation."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = (
    "FAULT_INJECTION_TOKEN",
    "INCIDENT_CONTROLLER_TOKEN",
    "DOCKER_EXECUTOR_TOKEN",
    "DOCKER_EXECUTOR_READ_TOKEN",
    "DIAGNOSTICS_TOKEN",
    "DIAGNOSTIC_POSTGRES_PASSWORD",
)


def values(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }


@pytest.fixture
def isolated_project(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy2(
        PROJECT_ROOT / "scripts/setup_env.py", tmp_path / "scripts/setup_env.py"
    )
    shutil.copy2(PROJECT_ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def run_setup(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(PROJECT_ROOT / "Makefile"), "setup"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "MAKEFLAGS": ""},
    )


def test_first_run_creates_private_env_without_printing_secrets(isolated_project):
    root = isolated_project
    result = run_setup(root)
    actual = values(root / ".env")
    assert result.stdout == result.stderr == ""
    assert actual["POSTGRES_PASSWORD"] == "devpassword"
    assert all(re.fullmatch(r"[0-9a-f]{64}", actual[key]) for key in SECRET_KEYS)
    assert len({actual[key] for key in SECRET_KEYS}) == len(SECRET_KEYS)
    assert all(actual[key] not in result.stdout + result.stderr for key in SECRET_KEYS)
    assert (root / ".env").stat().st_mode & 0o077 == 0


def test_repeat_run_keeps_existing_values_and_bytes(isolated_project):
    root = isolated_project
    run_setup(root)
    before = (root / ".env").read_bytes()
    result = run_setup(root)
    assert result.stdout == result.stderr == ""
    assert (root / ".env").read_bytes() == before


def test_existing_values_and_postgres_password_are_preserved(isolated_project):
    root = isolated_project
    env_file = root / ".env"
    initial = (root / ".env.example").read_text(encoding="utf-8")
    initial = initial.replace(
        "POSTGRES_PASSWORD=devpassword", "POSTGRES_PASSWORD=my-existing-db-password"
    )
    initial = initial.replace(
        "FAULT_INJECTION_TOKEN=", "FAULT_INJECTION_TOKEN=my-existing-token"
    )
    initial = initial.replace(
        "DIAGNOSTICS_TOKEN=", 'DIAGNOSTICS_TOKEN="quoted-existing-token"'
    )
    env_file.write_text(initial, encoding="utf-8")
    result = run_setup(root)
    actual = values(env_file)
    assert actual["POSTGRES_PASSWORD"] == "my-existing-db-password"
    assert actual["FAULT_INJECTION_TOKEN"] == "my-existing-token"
    assert actual["DIAGNOSTICS_TOKEN"] == '"quoted-existing-token"'
    assert all(actual[key] for key in SECRET_KEYS)
    assert "my-existing-token" not in result.stdout + result.stderr
    assert "my-existing-db-password" not in result.stdout + result.stderr
    after_first = env_file.read_bytes()
    run_setup(root)
    assert env_file.read_bytes() == after_first


def test_template_and_gitignore_are_safe():
    template = values(PROJECT_ROOT / ".env.example")
    assert all(key in template and template[key] == "" for key in SECRET_KEYS)
    assert ".env" in (PROJECT_ROOT / ".gitignore").read_text().splitlines()
