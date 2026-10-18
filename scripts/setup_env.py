"""Create local .env and fill only missing development credentials."""

import os
import re
import secrets
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = (
    "FAULT_INJECTION_TOKEN",
    "INCIDENT_CONTROLLER_TOKEN",
    "DOCKER_EXECUTOR_TOKEN",
    "DOCKER_EXECUTOR_READ_TOKEN",
    "DIAGNOSTICS_TOKEN",
    "DIAGNOSTIC_POSTGRES_PASSWORD",
)
ASSIGNMENT = re.compile(r"^([ \t]*([A-Z][A-Z0-9_]*)[ \t]*=[ \t]*)(.*?)(\r?\n?)$")


def is_empty(value: str) -> bool:
    stripped = value.strip()
    return stripped in {"", "''", '""'} or stripped.startswith("#")


def prepare_env(root: Path) -> bool:
    target = root / ".env"
    if target.exists():
        original = target.read_text(encoding="utf-8")
    else:
        original = (root / ".env.example").read_text(encoding="utf-8")

    lines = original.splitlines(keepends=True)
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = ASSIGNMENT.match(line)
        if match and match.group(2) in SECRET_KEYS:
            key = match.group(2)
            if key in positions:
                raise ValueError(f"Duplicate configuration key: {key}")
            positions[key] = index

    changed = not target.exists()
    for key in SECRET_KEYS:
        if key in positions:
            index = positions[key]
            match = ASSIGNMENT.match(lines[index])
            assert match is not None
            if not is_empty(match.group(3)):
                continue
            lines[index] = f"{match.group(1)}{secrets.token_hex(32)}{match.group(4)}"
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines.append("\n")
            lines.append(f"{key}={secrets.token_hex(32)}\n")
        changed = True

    if not changed:
        return False
    data = "".join(lines)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=root, prefix=".env.", delete=False
    ) as temporary:
        temp_path = Path(temporary.name)
        temporary.write(data)
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    prepare_env(PROJECT_ROOT)
