"""
Tests for services/listen_service.py: the read-only Listen config, the engine
and dependency checks behind a narration run, and the track lookup. Fully
mocked; the key guarantee is that no path, URL or secret leaks into a result.
"""
import json
import os

import pytest

from core import Line
from services import listen_service
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError

# A GPT-SoVITS address left in .env by an older version.
FAKE_URL = "http://secret-host.example:9999"


def _drama(db, lines=(), **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("content_mode", "novel_narration")
    did = db.create_drama(**fields)
    if lines:
        db.save_lines(did, list(lines))
    return did


def _line(idx, speaker=None, en="hi", zh="你好"):
    return Line(idx=idx, start=idx, end=idx + 1, zh=zh, en=en, speaker=speaker)


class TestConfig:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            listen_service.get_listen_config(999)
        with pytest.raises(NotFoundError):
            listen_service.get_listen_track(999)

    def test_a_title_that_is_not_a_narrated_novel_is_not_found(self, isolated_db):
        did = _drama(isolated_db, content_mode="drama")
        for call in (listen_service.get_listen_config, listen_service.get_listen_track):
            with pytest.raises(NotFoundError):
                call(did)
        with pytest.raises(NotFoundError):
            listen_service.start_listen(did, "omnivoice")

    def test_fresh_defaults(self, isolated_db):
        cfg = listen_service.get_listen_config(_drama(isolated_db))
        assert cfg["narration_language"] == "translation"
        assert cfg["narration_language_options"] == ["translation", "original"]
        assert [e["key"] for e in cfg["tts_engines"]] == ["omnivoice"]
        assert cfg["default_engine"] == "omnivoice"
        assert cfg["gpu_required"] is False
        assert cfg["track_available"] is False
        assert cfg["speakable_line_count"] == 0

    def test_an_unknown_stored_language_reads_as_translation(self, isolated_db):
        did = _drama(isolated_db, narration_language="bogus")
        assert listen_service.get_listen_config(did)["narration_language"] == "translation"

    def test_original_narration_counts_source_text(self, isolated_db):
        did = _drama(isolated_db, [_line(0, en=""), _line(1, en="")], narration_language="original")
        assert listen_service.get_listen_config(did)["speakable_line_count"] == 2
        did2 = _drama(isolated_db, [_line(0, en=""), _line(1, en="x")])
        assert listen_service.get_listen_config(did2)["speakable_line_count"] == 1

    def test_a_local_clone_makes_the_gpu_required(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", ref_text="t",
                                     clone_engine="omnivoice")
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        assert listen_service.get_listen_config(did)["gpu_required"] is True

    def test_no_leaks(self, isolated_db, monkeypatch):
        monkeypatch.setenv("BAIHE_GPT_SOVITS_URL", FAKE_URL)
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", ref_audio_filename="ref.wav", clone_engine="omnivoice")
        open(os.path.join(isolated_db.drama_dir(did), "ref.wav"), "wb").close()
        text = json.dumps(listen_service.get_listen_config(did))
        assert isolated_db.drama_dir(did) not in text
        assert FAKE_URL not in text and "secret-host" not in text

    def test_track_available_flips_with_the_narration_track_only(self, isolated_db):
        did = _drama(isolated_db)
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "dub_track.wav"), "wb").close()
        assert listen_service.get_listen_config(did)["track_available"] is False
        open(os.path.join(ddir, "narration_track.wav"), "wb").close()
        assert listen_service.get_listen_config(did)["track_available"] is True
        track = listen_service.get_listen_track(did)
        assert track["name"] == "narration_track.wav"

    def test_reads_do_not_create_the_title_folder(self, isolated_db):
        did = _drama(isolated_db, [_line(0, speaker="S1")])
        folder = os.path.join(isolated_db.DRAMAS_DIR, str(did))
        assert not os.path.exists(folder)
        listen_service.get_listen_config(did)
        with pytest.raises(NotFoundError):
            listen_service.get_listen_track(did)
        assert not os.path.exists(folder)


class TestRemovedEnginesAndBlockers:
    """A title that still names a removed engine loads, says so in plain
    words and refuses to generate; nothing stored is rewritten."""

    @pytest.fixture(autouse=True)
    def _engine_ready(self, monkeypatch):
        monkeypatch.setattr(listen_service, "_engine_install_problem", lambda engine: None)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")

    @pytest.mark.parametrize("key,label", [("f5tts", "F5-TTS"), ("tada", "TADA"),
                                           ("chatterbox", "Chatterbox"), ("gpt_sovits", "GPT-SoVITS")])
    def test_a_character_with_a_removed_engine_still_loads_and_blocks(self, isolated_db, key, label):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", character_name="Lin", clone_engine=key,
                                     ref_audio_filename="ref.wav", ref_text="t")
        cfg = listen_service.get_listen_config(did)
        assert cfg["blocker"] == f"Lin: The {label} engine was removed. Pick another voice engine."
        row = isolated_db.list_characters(did)[0]
        assert (row["clone_engine"], row["ref_audio_filename"], row["ref_text"]) == (key, "ref.wav", "t")

    def test_no_blocker_when_everything_is_fine(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        assert listen_service.get_listen_config(did)["blocker"] is None

    def test_no_engine_installed_is_one_plain_sentence(self, isolated_db, monkeypatch):
        monkeypatch.setattr(listen_service, "_engine_install_problem", lambda engine: "x is missing")
        did = _drama(isolated_db, [_line(0, "A")])
        cfg = listen_service.get_listen_config(did)
        assert cfg["blocker"] == "No voice engine is installed. Install one in Diagnostics."
        assert all(e["unavailable_reason"] == "x is missing" for e in cfg["tts_engines"])

    def test_an_installed_engine_clears_the_blocker(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        cfg = listen_service.get_listen_config(did)
        assert cfg["blocker"] is None
        assert [e["unavailable_reason"] for e in cfg["tts_engines"]] == [None]

    def test_original_language_narration_works_with_omnivoice_for_zh_ja_ko(self, isolated_db):
        for lang in ("zh", "ja", "ko"):
            did = _drama(isolated_db, [_line(0, "A", zh="你好", en="")], narration_language="original",
                         source_language=lang)
            reasons = {e["key"]: e["unavailable_reason"]
                       for e in listen_service.get_listen_config(did)["tts_engines"]}
            assert reasons == {"omnivoice": None}, lang

    def test_original_language_narration_is_refused_for_a_language_it_cannot_speak(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A", zh="hello", en="")], narration_language="original",
                     source_language="en")
        reasons = {e["key"]: e["unavailable_reason"]
                   for e in listen_service.get_listen_config(did)["tts_engines"]}
        assert reasons["omnivoice"] == ("OmniVoice can't speak this title's original language. "
                                        "Narrate the translation instead.")

    def test_translation_narration_leaves_every_engine_available(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")], narration_language="translation",
                     source_language="ko")
        assert all(e["unavailable_reason"] is None
                   for e in listen_service.get_listen_config(did)["tts_engines"])

    def test_start_refuses_every_removed_engine(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        for key, label in (("edge_tts", "Edge TTS"), ("offline", "Piper"), ("f5tts", "F5-TTS"),
                           ("tada", "TADA"), ("chatterbox", "Chatterbox"), ("gpt_sovits", "GPT-SoVITS")):
            with pytest.raises(InvalidInputError) as err:
                listen_service.start_listen(did, key)
            assert str(err.value) == f"The {label} engine was removed. Pick another voice engine."

    def test_start_refuses_original_narration_in_a_language_the_engine_cannot_speak(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A", zh="hello", en="")], narration_language="original",
                     source_language="en")
        with pytest.raises(DependencyUnavailableError, match="can't speak this title's original language"):
            listen_service.start_listen(did, "omnivoice")

    @pytest.mark.parametrize("key", ["tada", "chatterbox", "gpt_sovits"])
    def test_start_refuses_a_character_stored_with_a_removed_engine(self, isolated_db, key):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", clone_engine=key, voice_design="calm")
        with pytest.raises(InvalidInputError, match="engine was removed"):
            listen_service.start_listen(did, "omnivoice")
        assert isolated_db.list_characters(did)[0]["clone_engine"] == key
        assert not os.path.exists(os.path.join(isolated_db.drama_dir(did), "dub_clips"))

    def test_start_refuses_an_unknown_narration_language(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        with pytest.raises(InvalidInputError, match="Unknown narration language"):
            listen_service.start_listen(did, "omnivoice", "klingon")


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
        assert listen_service._missing_engine_dependency("omnivoice") == (
            "OmniVoice is not installed. Install it in Diagnostics.")

    def test_no_ffmpeg_is_reported_before_the_engine(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert "ffmpeg" in listen_service._missing_engine_dependency("omnivoice")

    def test_omnivoice_needs_a_new_enough_transformers(self, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
        for installed, ok in (("4.57.6", False), ("5.2.0", False), ("5.3.0", True), ("5.6.1", True)):
            monkeypatch.setattr(listen_service.importlib.metadata, "version", lambda name, v=installed: v)
            problem = listen_service._missing_engine_dependency("omnivoice")
            assert (problem is None) is ok, installed
            if not ok:
                assert problem == "OmniVoice needs a newer transformers than is installed. Check Diagnostics."

    def test_no_message_names_a_path_or_the_server_address(self, monkeypatch):
        monkeypatch.setenv("BAIHE_GPT_SOVITS_URL", FAKE_URL)
        for engine in listen_service.dub.CLONE_ENGINES:
            assert FAKE_URL not in (listen_service._missing_engine_dependency(engine) or "")
