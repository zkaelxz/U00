"""tests/test_build_release.py -- scripts/build_release.py's zip builder
(tmp dirs only; never runs npm)."""
import importlib.util
import json
import os
import zipfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "build_release", os.path.join(PROJECT_ROOT, "scripts", "build_release.py"))
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


def _fake_repo(tmp_path, version="1.2.3", with_index=True):
    frontend = tmp_path / "frontend"
    (frontend / "dist" / "assets").mkdir(parents=True)
    (frontend / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    if with_index:
        (frontend / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    (frontend / "dist" / "assets" / "app.js").write_text("x", encoding="utf-8")
    (frontend / "src.ts").write_text("not shipped", encoding="utf-8")
    return tmp_path


def test_zip_contains_only_frontend_dist_under_its_repo_path(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    zip_path = build_release.make_zip(repo)
    assert zip_path == repo / "dist" / "baihe-frontend-1.2.3.zip"
    with zipfile.ZipFile(zip_path) as zf:
        assert sorted(zf.namelist()) == [
            "frontend/dist/assets/app.js", "frontend/dist/index.html"]
        assert zf.read("frontend/dist/index.html") == b"<html></html>"
    assert not (repo / "dist" / "baihe-frontend-1.2.3.zip.tmp").exists()


def test_unzipping_into_a_checkout_puts_index_where_the_launcher_looks(tmp_path):
    zip_path = build_release.make_zip(_fake_repo(tmp_path / "repo"), out_dir=tmp_path / "out")
    checkout = tmp_path / "checkout"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(checkout)
    assert (checkout / "frontend" / "dist" / "index.html").is_file()


def test_missing_dist_is_a_plain_error(tmp_path):
    repo = _fake_repo(tmp_path / "repo", with_index=False)
    with pytest.raises(build_release.ReleaseError, match="npm run build"):
        build_release.make_zip(repo)


def test_missing_version_is_a_plain_error(tmp_path):
    repo = _fake_repo(tmp_path / "repo")
    (repo / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    with pytest.raises(build_release.ReleaseError, match="version"):
        build_release.make_zip(repo)
