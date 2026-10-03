"""Saving a comic series' chapters as CBZ files
(POST /api/sources/{name}/save, services/sources_save_service.py).
Fake adapters, no network; files go to a temp folder."""
import os
import threading
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from services import sources_save_service as save
from sources.models import FailureReason, SourceError
from tests.test_api_sources_import import _make, _result, _wait, client, fakes  # noqa: F401

JOB = "sources_save"


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = str(tmp_path / "saved")
    monkeypatch.setattr(save, "save_root", lambda: path)
    return path


def _start(client, name, ids, series="s1"):
    return client.post(f"/api/sources/{name}/save", json={"series_id": series, "chapter_ids": ids})


def _files(root):
    out = []
    for d, _, names in os.walk(root):
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return sorted(out)


def test_saves_chosen_chapters_as_cbz_in_reading_order(client, fakes, root):
    fakes["comicx"] = _make("comicx", comic=True)
    r = _start(client, "comicx", ["c10", "c1"])
    assert r.status_code == 200 and r.json()["job_id"] == JOB
    r, body = _result(client, JOB)
    res = body["result"]
    assert res["kind"] == "chapter_save" and res["saved_count"] == 2
    assert [c["chapter_id"] for c in res["chapters"]] == ["c1", "c10"]
    assert root not in r.text
    assert _files(root) == [os.path.join("Comicx", "Series T", "0001 第1章.cbz"),
                            os.path.join("Comicx", "Series T", "0003 第10章.cbz")]
    with zipfile.ZipFile(os.path.join(root, "Comicx", "Series T", "0001 第1章.cbz")) as zf:
        assert zf.namelist() == ["001.png", "002.png", "ComicInfo.xml"]
        info = zf.read("ComicInfo.xml").decode()
        assert "<Series>Series T</Series>" in info and "<Number>1</Number>" in info
        assert zf.read("001.png").startswith(b"\x89PNG")


def test_saving_again_skips_existing_files(client, fakes, root):
    calls = []
    fakes["comicx"] = _make("comicx", comic=True, calls=calls)
    _start(client, "comicx", ["c1"])
    _wait(JOB)
    _start(client, "comicx", ["c1", "c2"])
    _, body = _result(client, JOB)
    outcomes = {c["chapter_id"]: c["outcome"] for c in body["result"]["chapters"]}
    assert outcomes == {"c1": "skipped", "c2": "saved"}
    assert calls == ["c1", "c2"]


def test_failed_chapter_leaves_no_partial_file(client, fakes, root):
    err = SourceError("site down", FailureReason.SERVER_ERROR)
    fakes["comicx"] = _make("comicx", comic=True, fail={"c2": err})
    _start(client, "comicx", ["c1", "c2", "c10"])
    _, body = _result(client, JOB)
    res = body["result"]
    assert [c["outcome"] for c in res["chapters"]] == ["saved", "failed", "saved"]
    assert res["partial"] is True
    assert not any(f.endswith(".part") for f in _files(root))
    assert len(_files(root)) == 2


def test_unknown_chapter_ids_are_not_found(client, fakes, root):
    fakes["comicx"] = _make("comicx", comic=True)
    _start(client, "comicx", ["c1", "gone"])
    _, body = _result(client, JOB)
    assert body["result"]["chapters"][-1] == {"chapter_id": "gone", "title": "",
                                              "outcome": "not_found"}


def test_cancel_stops_and_lists_the_rest_as_not_attempted(client, fakes, root):
    gate = threading.Event()
    fakes["comicx"] = _make("comicx", comic=True, gate=gate)
    _start(client, "comicx", ["c1", "c2"])
    import background_jobs
    background_jobs.request_cancel(JOB)
    gate.set()
    _, body = _result(client, JOB)
    res = body["result"]
    assert res["cancelled"] is True
    assert {c["outcome"] for c in res["chapters"]} == {"not_attempted"}
    assert _files(root) == []


def test_text_sources_and_bad_ids_are_refused(client, fakes, root):
    fakes["novelx"] = _make("novelx")
    fakes["comicx"] = _make("comicx", comic=True)
    assert _start(client, "novelx", ["c1"]).status_code == 400
    assert _start(client, "comicx", ["../x"]).status_code == 422
    assert _start(client, "comicx", ["c1"], series="//evil").status_code == 422
    assert _start(client, "nope", ["c1"]).status_code == 404


def test_one_save_at_a_time(client, fakes, root):
    gate = threading.Event()
    fakes["comicx"] = _make("comicx", comic=True, gate=gate)
    assert _start(client, "comicx", ["c1"]).status_code == 200
    assert _start(client, "comicx", ["c2"]).status_code == 409
    gate.set()
    _wait(JOB)


class TestNames:
    def test_site_text_becomes_a_safe_file_name(self):
        assert save.safe_name('a/b\\c:d*?"<>|e', "x") == "a b c d e"
        assert save.safe_name("..", "x") == "x"
        assert save.safe_name("  ", "x") == "x"
        assert save.safe_name("CON", "x") == "CON_"
        assert save.safe_name("Chapter 1...", "x") == "Chapter 1"
        assert len(save.safe_name("y" * 500, "x")) == save.MAX_NAME

    def test_paths_stay_inside_the_save_folder(self, tmp_path):
        path = save.chapter_path(str(tmp_path), "../..", "../../etc", 7, "../passwd")
        assert os.path.commonpath([str(tmp_path), path]) == str(tmp_path)
        assert os.path.basename(path) == "0007 passwd.cbz"
