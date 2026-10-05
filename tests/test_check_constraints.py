"""scripts/check_constraints.py: requirements, constraints.txt and the installer lock must agree."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("packaging")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_constraints", ROOT / "scripts" / "check_constraints.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _tree(tmp_path, requirements, constraints, lock=None):
    (tmp_path / "requirements-core.txt").write_text(requirements, encoding="utf-8")
    (tmp_path / "requirements-media.txt").write_text("", encoding="utf-8")
    (tmp_path / "requirements-optional.txt").write_text("", encoding="utf-8")
    (tmp_path / "constraints.txt").write_text(constraints, encoding="utf-8")
    if lock is not None:
        (tmp_path / "installer").mkdir()
        (tmp_path / "installer" / "wheels.lock.txt").write_text(lock, encoding="utf-8")
    return tmp_path


def test_the_repo_files_agree():
    assert cc.check(ROOT) == []


def test_agreeing_files_report_nothing(tmp_path):
    root = _tree(tmp_path, "fastapi>=0.115  # api\ntorch>=2.0\n",
                 "fastapi==0.142.1  # pin\ntorch<3\n",
                 "fastapi==0.142.1 \\\n    --hash=sha256:abc\n")
    assert cc.check(root) == []


def test_pin_older_than_the_requirements_floor(tmp_path):
    root = _tree(tmp_path, "fastapi>=0.150\n", "fastapi==0.142.1\n")
    problems = cc.check(root)
    assert len(problems) == 1
    assert "fastapi" in problems[0] and "0.142.1" in problems[0] and "bump the pin" in problems[0]


def test_lock_older_than_the_requirements_floor(tmp_path):
    root = _tree(tmp_path, "httpx>=0.29\n", "", "httpx==0.28.1 \\\n    --hash=sha256:abc\n")
    problems = cc.check(root)
    assert len(problems) == 1
    assert "wheels.lock.txt" in problems[0] and "regenerate the lock" in problems[0]


def test_cap_below_the_requirements_floor(tmp_path):
    root = _tree(tmp_path, "authlib>=2.0\n", "authlib<2\n")
    problems = cc.check(root)
    assert len(problems) == 1 and "no common version" in problems[0]


def test_names_are_matched_after_normalising(tmp_path):
    root = _tree(tmp_path, "Python_Multipart>=0.0.40\n", "python-multipart==0.0.32\n")
    assert len(cc.check(root)) == 1


def test_main_lists_every_problem_and_exits_1(tmp_path, capsys):
    root = _tree(tmp_path, "fastapi>=0.150\nauthlib>=2.0\n", "fastapi==0.142.1\nauthlib<2\n")
    assert cc.main(root) == 1
    out = capsys.readouterr().out
    assert "fastapi" in out and "authlib" in out and "docs/testing-and-ci.md" in out


def test_main_passes_on_agreeing_files(tmp_path, capsys):
    assert cc.main(_tree(tmp_path, "torch>=2.0\n", "torch<3\n")) == 0
