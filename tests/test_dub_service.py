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
from services import dub_service
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError

# A GPT-SoVITS address left in .env by an older version.
FAKE_URL = "http://secret-host.example:9999"


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
        assert [e["key"] for e in cfg["tts_engines"]] == ["omnivoice"]
        assert cfg["default_engine"] == "omnivoice"
        assert cfg["speakers"] == []
        assert cfg["gpu_required"] is False
        assert cfg["track_available"] is False
        assert "gpt_sovits_configured" not in cfg

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

    def test_speakers_are_listed_with_the_default_engines_voice(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A"), _line(1, "B"), _line(2, "C")])
        isolated_db.upsert_character(did, "B", character_name="Bob")
        speakers = {s["speaker_label"]: s for s in dub_service.get_dub_config(did)["speakers"]}
        assert set(speakers) == {"A", "B", "C"}
        assert speakers["B"]["character_name"] == "Bob"
        assert {s["engine"] for s in speakers.values()} == {"omnivoice"}
        assert all(set(s) == {"speaker_label", "character_name", "engine", "has_clone_ref", "clone_warning"}
                   for s in speakers.values())

    def test_saved_voice_names_of_the_removed_engines_are_ignored_not_shown(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", tts_voice="en-US-AvaNeural", offline_voice="en_US-amy-medium")
        cfg = dub_service.get_dub_config(did)
        assert "en-US-AvaNeural" not in json.dumps(cfg) and "amy" not in json.dumps(cfg)
        row = isolated_db.list_characters(did)[0]
        assert (row["tts_voice"], row["offline_voice"]) == ("en-US-AvaNeural", "en_US-amy-medium")

    def test_has_clone_ref_only_when_file_exists(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", ref_text="t",
                                     clone_engine="omnivoice")
        sp = dub_service.get_dub_config(did)["speakers"][0]
        assert sp["engine"] == "omnivoice" and sp["has_clone_ref"] is False
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        cfg = dub_service.get_dub_config(did)
        assert cfg["speakers"][0]["has_clone_ref"] is True
        assert cfg["gpu_required"] is True

    def test_no_leaks(self, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_GPT_SOVITS_URL", FAKE_URL)
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", clone_engine="omnivoice")
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        cfg = dub_service.get_dub_config(did)
        text = json.dumps(cfg)
        assert isolated_db.drama_dir(did) not in text
        assert FAKE_URL not in text and "secret-host" not in text

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


class TestRemovedEnginesAndBlockers:
    """A title that still names a removed engine loads, says so in plain
    words and refuses to generate; nothing stored is rewritten."""

    @pytest.fixture(autouse=True)
    def _engine_ready(self, monkeypatch):
        monkeypatch.setattr(dub_service, "_engine_install_problem", lambda engine: None)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")

    @pytest.mark.parametrize("key,label", [("f5tts", "F5-TTS"), ("tada", "TADA"),
                                           ("chatterbox", "Chatterbox"), ("gpt_sovits", "GPT-SoVITS")])
    def test_a_character_with_a_removed_engine_still_loads_and_reads_as_removed(
            self, isolated_db, key, label):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", character_name="Lin", clone_engine=key,
                                     ref_audio_filename="ref.wav", ref_text="t")
        cfg = dub_service.get_dub_config(did)
        removed = f"The {label} engine was removed. Pick another voice engine in Dub."
        assert cfg["speakers"][0]["clone_warning"] == removed
        assert cfg["speakers"][0]["engine"] == key
        assert cfg["blocker"] == f"Lin: {removed}"
        row = isolated_db.list_characters(did)[0]
        assert (row["clone_engine"], row["ref_audio_filename"], row["ref_text"]) == (key, "ref.wav", "t")

    def test_no_blocker_when_everything_is_fine(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        assert dub_service.get_dub_config(did)["blocker"] is None

    def test_no_engine_installed_is_one_plain_sentence(self, isolated_db, monkeypatch):
        monkeypatch.setattr(dub_service, "_engine_install_problem", lambda engine: "x is missing")
        did = _drama(isolated_db, [_line(0, "A")])
        cfg = dub_service.get_dub_config(did)
        assert cfg["blocker"] == "No voice engine is installed. Install one in Diagnostics."
        assert all(e["unavailable_reason"] == "x is missing" for e in cfg["tts_engines"])

    def test_an_installed_engine_clears_the_blocker(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        cfg = dub_service.get_dub_config(did)
        assert cfg["blocker"] is None
        assert [e["unavailable_reason"] for e in cfg["tts_engines"]] == [None]

    def test_original_language_narration_works_with_omnivoice_for_zh_ja_ko(self, isolated_db):
        for lang in ("zh", "ja", "ko"):
            did = _drama(isolated_db, [_line(0, "A", zh="你好", en="")], content_mode="novel_narration",
                         narration_language="original", source_language=lang)
            reasons = {e["key"]: e["unavailable_reason"]
                       for e in dub_service.get_dub_config(did)["tts_engines"]}
            assert reasons == {"omnivoice": None}, lang

    def test_original_language_narration_is_refused_for_a_language_it_cannot_speak(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A", zh="hello", en="")], content_mode="novel_narration",
                     narration_language="original", source_language="en")
        reasons = {e["key"]: e["unavailable_reason"]
                   for e in dub_service.get_dub_config(did)["tts_engines"]}
        assert reasons["omnivoice"] == ("OmniVoice can't speak this title's original language. "
                                        "Narrate the translation instead.")

    def test_translation_narration_leaves_every_engine_available(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")], content_mode="novel_narration",
                     narration_language="translation", source_language="ko")
        assert all(e["unavailable_reason"] is None
                   for e in dub_service.get_dub_config(did)["tts_engines"])

    def test_start_refuses_every_removed_engine(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        for key, label in (("edge_tts", "Edge TTS"), ("offline", "Piper"), ("f5tts", "F5-TTS"),
                           ("tada", "TADA"), ("chatterbox", "Chatterbox"), ("gpt_sovits", "GPT-SoVITS")):
            with pytest.raises(InvalidInputError) as err:
                dub_service.start_dub_run(did, tts_engine=key)
            assert str(err.value) == f"The {label} engine was removed. Pick another voice engine in Dub."

    def test_start_refuses_original_narration_in_a_language_the_engine_cannot_speak(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A", zh="hello", en="")], content_mode="novel_narration",
                     narration_language="original", source_language="en")
        with pytest.raises(DependencyUnavailableError, match="can't speak this title's original language"):
            dub_service.start_dub_run(did, tts_engine="omnivoice")

    @pytest.mark.parametrize("key", ["tada", "chatterbox", "gpt_sovits"])
    def test_start_refuses_a_character_stored_with_a_removed_engine(self, isolated_db, key):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", clone_engine=key, voice_design="calm")
        with pytest.raises(InvalidInputError, match="engine was removed"):
            dub_service.start_dub_run(did)
        assert isolated_db.list_characters(did)[0]["clone_engine"] == key
        assert not os.path.exists(os.path.join(isolated_db.drama_dir(did), "dub_clips"))


class TestEngineInstallChecks:
    """_engine_install_problem against what is really importable here."""

    @pytest.fixture(autouse=True)
    def _ffmpeg(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")

    def _without(self, monkeypatch, *modules):
        import importlib.util
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name in modules else real(name, *a, **k))

    def test_a_missing_package_is_named_with_the_fix(self, monkeypatch):
        self._without(monkeypatch, "omnivoice")
        assert dub_service._missing_engine_dependency("omnivoice") == (
            "OmniVoice is not installed. Install it in Diagnostics.")

    def test_no_ffmpeg_is_reported_before_the_engine(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert "ffmpeg" in dub_service._missing_engine_dependency("omnivoice")

    def test_omnivoice_needs_a_new_enough_transformers(self, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
        for installed, ok in (("4.57.6", False), ("5.2.0", False), ("5.3.0", True), ("5.6.1", True)):
            monkeypatch.setattr(dub_service.importlib.metadata, "version", lambda name, v=installed: v)
            problem = dub_service._missing_engine_dependency("omnivoice")
            assert (problem is None) is ok, installed
            if not ok:
                assert problem == "OmniVoice needs a newer transformers than is installed. Check Diagnostics."

    def test_no_message_names_a_path_or_the_server_address(self, monkeypatch):
        monkeypatch.setenv("BAIHE_GPT_SOVITS_URL", FAKE_URL)
        for engine in dub_service.dub.CLONE_ENGINES:
            assert FAKE_URL not in (dub_service._missing_engine_dependency(engine) or "")


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


# --- Hardening H3 ---------------------------------------------------------

def test_h3_reads_do_not_create_drama_folder(isolated_db):
    did = _drama(isolated_db, lines=[_line(0, speaker="S1")])
    folder = os.path.join(isolated_db.DRAMAS_DIR, str(did))
    assert not os.path.exists(folder)
    dub_service.get_dub_config(did)
    dub_service.get_dub_pacing(did)
    assert not os.path.exists(folder)


def test_h3_pacing_skips_malformed_records(isolated_db):
    did = _drama(isolated_db, lines=[_line(0, dub_filename="a.wav"), _line(1, dub_filename="b.wav")])
    _write_pacing(isolated_db, did, {
        "0": "not-a-dict",
        "1": {"status": "fit", "factor": 1.0, "clip_ms": 10.5, "window_ms": 20.5, "dub_filename": "b.wav"}})
    out = dub_service.get_dub_pacing(did)
    assert [ln["idx"] for ln in out["lines"]] == [1]
    assert out["lines"][0]["clip_ms"] == 10.5


def test_resolve_pacing_limits_defaults_and_ranges():
    assert dub_service.resolve_pacing_limits(None, None) == (
        dub_service.dub.DUB_MAX_SPEEDUP, dub_service.dub.DUB_MAX_SLOWDOWN)
    assert dub_service.resolve_pacing_limits(1.5, 0.8) == (1.5, 0.8)
    for bad in [(0, None), (2.1, None), (None, 0), (None, 1.1), (0.9, None)]:
        with pytest.raises(InvalidInputError, match="out of range"):
            dub_service.resolve_pacing_limits(*bad)
