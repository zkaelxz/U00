"""The local-coder kit: the lean pytest flags, tools/check_change.py and the clone-parent shadow guard."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import core

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_change", ROOT / "tools" / "check_change.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

CRASHING_THREAD = "import threading\n\ndef test_crash():\n    t = threading.Thread(target=lambda: 1 / 0)\n    t.start()\n    t.join()\n"
GUARD_INI = "[pytest]\nfilterwarnings =\n    error::pytest.PytestUnhandledThreadExceptionWarning\n"


def _run_lean(tmp_path, flags):
    (tmp_path / "pytest.ini").write_text(GUARD_INI, encoding="utf-8")
    (tmp_path / "test_t.py").write_text(CRASHING_THREAD, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--no-header", *flags, "test_t.py"],
                          cwd=tmp_path, capture_output=True, text=True).returncode


def test_lean_flags_keep_the_thread_exception_guard(tmp_path):
    flags = cc.LEAN_PYTEST.split()
    flags = [f for f in flags[flags.index("-W"):] if f not in ("-x", "--tb=short", "--no-header")]
    assert _run_lean(tmp_path, flags) != 0


def test_a_blanket_ignore_would_hide_a_crashing_thread(tmp_path):
    # Why the docs forbid it: this is the failure the narrow -W flags avoid.
    assert _run_lean(tmp_path, ["-W", "ignore"]) == 0


def test_check_change_maps_by_name_and_import():
    by_name, by_import = cc.map_tests(["services/drama_service.py"])
    assert "tests/test_drama_service.py" in by_name
    assert not set(by_name) & set(by_import)


def test_check_change_always_adds_guards_and_caps_shared_modules():
    kept, total = cc.plan(["core.py"], 5)
    assert len(kept) == 5 and total > 5
    assert not set(kept) & set(cc.GUARDS)


def test_check_change_frontend_steps_name_changed_tests():
    steps = cc.frontend_steps(["frontend/src/pages/x/Card.test.tsx", "frontend/src/pages/x/Card.tsx"])
    assert steps[0].endswith("npx tsc -b")
    assert steps[1].endswith("npx vitest run src/pages/x/Card.test.tsx")
    assert cc.frontend_steps(["docs/STATUS.md"]) == []


def test_modules_come_from_this_clone_not_its_parent_folder():
    # The repo root has __init__.py, so pytest puts the clone's parent on sys.path first; a stray
    # core.py or cli.py there would shadow the real module.
    assert Path(core.__file__).resolve().parent == ROOT


def _fake_runner(monkeypatch, failing_step=None):
    ran = []

    def fake_run(step, **kwargs):
        ran.append(step)
        code = 1 if failing_step and failing_step in step else 0
        return subprocess.CompletedProcess(step, code)

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    return ran


def test_check_change_run_executes_the_printed_steps_and_exits_zero(monkeypatch):
    ran = _fake_runner(monkeypatch)
    monkeypatch.setattr(cc, "changed_files", lambda base: (["frontend/src/pages/x/Card.tsx"], ""))
    assert cc.main(["--run"]) == 0
    assert ran[0].startswith(cc.LEAN_PYTEST) and all(g in ran[0] for g in cc.GUARDS)
    assert ran[1].endswith("npx tsc -b")


def test_check_change_run_stops_at_the_first_failing_step(monkeypatch):
    ran = _fake_runner(monkeypatch, failing_step="pytest")
    monkeypatch.setattr(cc, "changed_files", lambda base: (["frontend/src/pages/x/Card.tsx"], ""))
    assert cc.main(["--run"]) == 1
    assert len(ran) == 1


def test_check_change_default_prints_without_running(monkeypatch, capsys):
    ran = _fake_runner(monkeypatch)
    monkeypatch.setattr(cc, "changed_files", lambda base: (["docs/STATUS.md"], ""))
    assert cc.main([]) == 0
    assert ran == []
    out = capsys.readouterr().out
    assert out.startswith("changed: 1 files vs ") and cc.LEAN_PYTEST in out


def test_every_ci_guard_listed_in_ci_guards_txt_exists_and_is_in_the_change_guards():
    from pathlib import Path
    listed = (Path(cc.ROOT) / "tests" / "ci_guards.txt").read_text(encoding="utf-8").split()
    assert listed and all((Path(cc.ROOT) / name).is_file() for name in listed)
    assert set(listed) <= set(cc.GUARDS)
