"""
Tests for services/dub_service.py (Migration Slice 25): read-only Dub-stage
config and pacing. Fully mocked; the key guarantee is that no path, URL or
secret leaks into either result.
"""
import json
import os

import pytest

import dub
from core import Line
from services import dub_service, settings_service
from services.service_errors import NotFoundError

FAKE_URL = "http://secret-host.example:9999"


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)


def _drama(db, lines=(), **fields):
    fields.setdefault("title_en", "D")
    did = db.create_drama(**fields)
    if lines:
        db.save_lines(did, list(lines))
    return did


def _line(idx, speaker=None, en="hi", zh="你好", dub_filename=None):
    return Line(idx=idx, start=idx, end=idx + 1, zh=zh, en=en, speaker=speaker, dub_filename=dub_filename)


def _write_pacing(db, did, records):
    d = os.path.join(db.drama_dir(did), "dub_clips")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, dub.PACING_FILENAME), "w", encoding="utf-8") as f:
        json.dump(records, f)


class TestConfig:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            dub_service.get_dub_config(999)
        with pytest.raises(NotFoundError):
            dub_service.get_dub_pacing(999)

    def test_fresh_defaults(self, isolated_db):
        cfg = dub_service.get_dub_config(_drama(isolated_db))
        assert cfg["is_narration"] is False
        assert cfg["narration_language"] == "translation"
        assert cfg["source_language"] == "zh"
        assert cfg["defaults"] == {"max_speedup": dub.DUB_MAX_SPEEDUP, "max_slowdown": dub.DUB_MAX_SLOWDOWN,
                                   "speedup_range": [1.0, 2.0], "slowdown_range": [0.5, 1.0]}
        assert [e["key"] for e in cfg["tts_engines"]] == ["edge_tts", "offline"]
        assert cfg["speakers"] == []
        assert cfg["gpu_required"] is False
        assert cfg["track_available"] is False
        assert cfg["gpt_sovits_configured"] is False

    def test_narration(self, isolated_db):
        did = _drama(isolated_db, content_mode="novel_narration", narration_language="bogus")
        cfg = dub_service.get_dub_config(did)
        assert cfg["is_narration"] is True
        assert cfg["defaults"] is None
        assert cfg["narration_language"] == "translation"

    def test_narrate_original_counts_source_text(self, isolated_db):
        did = _drama(isolated_db, [_line(0, en=""), _line(1, en="")],
                     content_mode="novel_narration", narration_language="original")
        assert dub_service.get_dub_config(did)["speakable_line_count"] == 2
        did2 = _drama(isolated_db, [_line(0, en=""), _line(1, en="x")])
        assert dub_service.get_dub_config(did2)["speakable_line_count"] == 1

    def test_speakers_get_distinct_voices_and_assigned_respected(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A"), _line(1, "B"), _line(2, "C")])
        isolated_db.upsert_character(did, "B", character_name="Bob", tts_voice="custom-voice")
        speakers = {s["speaker_label"]: s for s in dub_service.get_dub_config(did)["speakers"]}
        assert set(speakers) == {"A", "B", "C"}
        assert speakers["B"]["edge_voice"] == "custom-voice"
        assert speakers["B"]["character_name"] == "Bob"
        assert speakers["A"]["edge_voice"] != speakers["C"]["edge_voice"]
        assert speakers["A"]["engine"] == "edge"
        assert all(s["offline_voice"] for s in speakers.values())

    def test_has_clone_ref_only_when_file_exists(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", ref_text="t",
                                     clone_engine="f5tts")
        sp = dub_service.get_dub_config(did)["speakers"][0]
        assert sp["engine"] == "f5tts" and sp["has_clone_ref"] is False
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        cfg = dub_service.get_dub_config(did)
        assert cfg["speakers"][0]["has_clone_ref"] is True
        assert cfg["gpu_required"] is True

    def test_no_leaks(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a, **kw: FAKE_URL if k == "gpt_sovits_url" else None)
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", clone_engine="gpt_sovits")
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        cfg = dub_service.get_dub_config(did)
        text = json.dumps(cfg)
        assert isolated_db.drama_dir(did) not in text
        assert FAKE_URL not in text and "secret-host" not in text
        assert cfg["gpt_sovits_configured"] is True

    def test_track_available_flips(self, isolated_db):
        did = _drama(isolated_db)
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "narration_track.wav"), "wb").close()
        assert dub_service.get_dub_config(did)["track_available"] is False
        open(os.path.join(ddir, "dub_track.wav"), "wb").close()
        assert dub_service.get_dub_config(did)["track_available"] is True

    def test_narration_track_file(self, isolated_db):
        did = _drama(isolated_db, content_mode="novel_narration")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "narration_track.wav"), "wb").close()
        assert dub_service.get_dub_config(did)["track_available"] is True


class TestPacing:
    def test_none_available(self, isolated_db):
        res = dub_service.get_dub_pacing(_drama(isolated_db, [_line(0, dub_filename="a.wav")]))
        assert res == {"available": False, "counts": {"fit": 0, "stretched": 0, "overflow": 0},
                       "lines": []}

    def test_counts_and_stale_excluded(self, isolated_db):
        did = _drama(isolated_db, [_line(0, dub_filename="a.wav"), _line(1, dub_filename="b.wav"),
                                   _line(2, dub_filename="new.wav"), _line(3)])
        rec = lambda status, fn, factor=1.0: {"status": status, "factor": factor, "clip_ms": 2000,
                                              "window_ms": 1500, "dub_filename": fn}
        _write_pacing(isolated_db, did, {
            "0": rec("fit", "a.wav"), "1": rec("stretched", "b.wav", 1.3),
            "2": rec("overflow", "old.wav", 1.4), "3": rec("fit", "x.wav")})
        res = dub_service.get_dub_pacing(did)
        assert res["available"] is True
        assert res["counts"] == {"fit": 1, "stretched": 1, "overflow": 0}
        assert [(l["idx"], l["status"]) for l in res["lines"]] == [(0, "fit"), (1, "stretched")]
        assert res["lines"][1] == {"idx": 1, "status": "stretched", "factor": 1.3,
                                   "clip_ms": 2000, "window_ms": 1500}
        assert isolated_db.drama_dir(did) not in json.dumps(res)

    def test_narration_unavailable(self, isolated_db):
        did = _drama(isolated_db, [_line(0, dub_filename="a.wav")], content_mode="novel_narration")
        _write_pacing(isolated_db, did, {"0": {"status": "fit", "factor": 1.0, "clip_ms": 1,
                                               "window_ms": 1, "dub_filename": "a.wav"}})
        res = dub_service.get_dub_pacing(did)
        assert res["available"] is False and res["lines"] == []
