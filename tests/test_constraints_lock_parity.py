"""constraints.txt exact pins must match installer/wheels.lock.txt."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pins(path, pattern):
    lines = path.read_text(encoding="utf-8").splitlines()
    return {m.group(1).lower().replace("_", "-"): m.group(2)
            for m in (re.match(pattern, line) for line in lines) if m}


def test_exact_constraint_pins_equal_the_installer_lock():
    constraints = _pins(ROOT / "constraints.txt", r"^([A-Za-z0-9_.\-]+)==([^\s#]+)")
    lock = _pins(ROOT / "installer" / "wheels.lock.txt", r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)")
    assert {"fastapi", "pydantic", "starlette", "python-multipart", "uvicorn"} <= set(constraints)
    wrong = {n: (v, lock.get(n)) for n, v in constraints.items() if lock.get(n) != v}
    assert not wrong, f"constraints.txt vs installer/wheels.lock.txt (constraints, lock): {wrong}"
