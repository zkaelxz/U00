"""lib/atomic_write.py: the one temp-file-then-replace writer, and the services that use it."""
import json
import os

import pytest

import core
from lib import atomic_write as aw


def _boom(*a, **k):
    raise OSError("boom")


def test_core_re_exports_the_lib_writer():
    assert core.atomic_write is aw.atomic_write


def test_text_and_binary_round_trip(tmp_path):
    aw.atomic_write(str(tmp_path / "a.txt"), "héllo")
    aw.atomic_write(str(tmp_path / "b.bin"), b"\x00\x01", binary=True)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "héllo"
    assert (tmp_path / "b.bin").read_bytes() == b"\x00\x01"


def test_replaces_an_existing_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old")
    aw.atomic_write(str(target), "new")
    assert target.read_text() == "new"
    assert os.listdir(tmp_path) == ["a.txt"]


@pytest.mark.parametrize("fail", ["replace", "write"])
def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp(tmp_path, monkeypatch, fail):
    target = tmp_path / "a.txt"
    target.write_text("old")
    if fail == "replace":
        monkeypatch.setattr(aw.os, "replace", _boom)
        data = "new"
    else:
        data = None                       # f.write(None) raises TypeError after the temp exists
    with pytest.raises((OSError, TypeError)):
        aw.atomic_write(str(target), data)
    assert target.read_text() == "old"
    assert os.listdir(tmp_path) == ["a.txt"]


def test_prefix_and_suffix_shape_the_temp_name(tmp_path, monkeypatch):
    seen = []
    real = aw.os.replace
    monkeypatch.setattr(aw.os, "replace", lambda src, dst: seen.append(os.path.basename(src)) or real(src, dst))
    aw.atomic_write(str(tmp_path / "a.txt"), "x", prefix=".keep_", suffix=".part")
    assert seen[0].startswith(".keep_") and seen[0].endswith(".part")


def test_novel_files_keep_a_name_the_temp_cleanup_does_not_match(isolated_db, monkeypatch):
    from services import novel_files_service as nf
    drama = isolated_db.create_drama(title_en="n")
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: seen.append(os.path.basename(src)) or real(src, dst))
    nf._write_atomic(drama, "novel.txt", "hello")
    assert seen and not seen[0].endswith((".tmp", ".tmp.png"))


def test_a_bare_filename_writes_into_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    aw.atomic_write("bare.txt", "x")
    assert os.listdir(tmp_path) == ["bare.txt"]


class TestVoiceCloneManifest:
    def test_an_unserialisable_manifest_leaves_no_temp_and_keeps_the_old_one(self, isolated_db):
        from services import voice_clone_service as vc
        drama = isolated_db.create_drama(title_en="t")
        vc._save_manifest(drama, {"a": 1})
        cdir = vc._candidates_dir(drama)
        with pytest.raises(TypeError):
            vc._save_manifest(drama, {"a": object()})
        assert sorted(os.listdir(cdir)) == [vc.MANIFEST_NAME]
        with open(os.path.join(cdir, vc.MANIFEST_NAME), encoding="utf-8") as fh:
            assert json.load(fh) == {"a": 1}


class TestBugReportWrite:
    def test_a_failed_write_leaves_no_fixed_name_temp(self, tmp_path):
        from services import bug_report_service as br
        target = tmp_path / "report.md"
        target.write_text("old")
        with pytest.raises(TypeError):
            br._write(str(target), None)
        assert os.listdir(tmp_path) == ["report.md"]

    def test_an_existing_fixed_name_file_is_not_used_or_clobbered(self, tmp_path):
        from services import bug_report_service as br
        bystander = tmp_path / "report.md.tmp"
        bystander.write_text("another writer's temp")
        br._write(str(tmp_path / "report.md"), "mine")
        assert (tmp_path / "report.md").read_text() == "mine"
        assert bystander.read_text() == "another writer's temp"

    def test_binary_writes_stay_binary(self, tmp_path):
        from services import bug_report_service as br
        br._write(str(tmp_path / "shot.png"), b"\x89PNG", binary=True)
        assert (tmp_path / "shot.png").read_bytes() == b"\x89PNG"
