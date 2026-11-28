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
from voice import dub
from lib.errors import ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError

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
        assert [e["key"] for e in cfg["tts_engines"]] == ["omnivoice", "qwen3_tts", "chatterbox"]
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

    def test_a_flac_track_is_found_and_wins_over_an_older_wav(self, isolated_db):
        did = _drama(isolated_db)
        ddir = isolated_db.drama_dir(did)
        open(os.path.join(ddir, "narration_track.wav"), "wb").close()
        open(os.path.join(ddir, "narration_track.flac"), "wb").close()
        assert listen_service.get_listen_config(did)["track_available"] is True
        assert listen_service.get_listen_track(did)["name"] == "narration_track.flac"

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
                                           ("gpt_sovits", "GPT-SoVITS")])
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
        assert [e["unavailable_reason"] for e in cfg["tts_engines"]] == [None, None, None]

    def test_original_language_narration_works_with_every_engine_for_zh_ja_ko(self, isolated_db):
        for lang in ("zh", "ja", "ko"):
            did = _drama(isolated_db, [_line(0, "A", zh="你好", en="")], narration_language="original",
                         source_language=lang)
            reasons = {e["key"]: e["unavailable_reason"]
                       for e in listen_service.get_listen_config(did)["tts_engines"]}
            assert reasons == {"omnivoice": None, "qwen3_tts": None, "chatterbox": None}, lang

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
                           ("tada", "TADA"), ("gpt_sovits", "GPT-SoVITS")):
            with pytest.raises(InvalidInputError) as err:
                listen_service.start_listen(did, key)
            assert str(err.value) == f"The {label} engine was removed. Pick another voice engine."

    def test_start_refuses_original_narration_in_a_language_the_engine_cannot_speak(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A", zh="hello", en="")], narration_language="original",
                     source_language="en")
        with pytest.raises(DependencyUnavailableError, match="can't speak this title's original language"):
            listen_service.start_listen(did, "omnivoice")

    @pytest.mark.parametrize("key", ["tada", "gpt_sovits"])
    def test_start_refuses_a_character_stored_with_a_removed_engine(self, isolated_db, key):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", clone_engine=key, voice_design="female, low pitch")
        with pytest.raises(InvalidInputError, match="engine was removed"):
            listen_service.start_listen(did, "omnivoice")
        assert isolated_db.list_characters(did)[0]["clone_engine"] == key
        assert not os.path.exists(os.path.join(isolated_db.drama_dir(did), "dub_clips"))

    def test_start_refuses_an_unknown_narration_language(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        with pytest.raises(InvalidInputError, match="Unknown narration language"):
            listen_service.start_listen(did, "omnivoice", "klingon")


class TestVoiceDescriptions:
    def test_a_description_omnivoice_would_refuse_blocks_the_run_with_the_words_it_accepts(self, isolated_db):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        isolated_db.upsert_character(did, "A", character_name="Lin", voice_design="warm audiobook narrator")
        blocker = listen_service.config_blocker(isolated_db.list_characters(did))
        assert "Lin" in blocker and "warm audiobook narrator" in blocker and "young adult" in blocker

    def test_a_description_is_only_checked_where_it_would_be_spoken(self, isolated_db):
        did = isolated_db.create_drama(title_en="N", content_mode="novel_narration")
        isolated_db.upsert_character(did, "A", voice_design="warm", ref_audio_filename="voices/a.wav")
        isolated_db.upsert_character(did, "B", voice_design="男，老年，低音调")
        assert dub.engine_blockers(isolated_db.list_characters(did), "omnivoice") == []

    def test_two_of_a_kind_or_an_accent_with_a_dialect_is_refused(self):
        assert dub.voice_description_problem("male, female")
        assert dub.voice_description_problem("british accent, 四川话")
        assert dub.voice_description_problem(" Female ,  Elderly, whisper ") is None
        assert all(dub.voice_description_problem(d) is None for d in dub.DEFAULT_VOICE_DESCRIPTIONS)


class TestSpeakersWithoutAVoice:
    """Only OmniVoice gives a bare speaker a default voice; the other engines need a clip or a description."""

    @pytest.fixture(autouse=True)
    def _engines_installed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(listen_service, "_engine_install_problem", lambda engine: None)

    def _title(self, db):
        did = _drama(db, [_line(0, "A"), _line(1, "B"), _line(2, "B", en="")])
        db.upsert_character(did, "A", character_name="Lin", voice_design="female, low pitch")
        db.upsert_character(did, "B", character_name="Wei")
        return did

    def test_config_names_them_per_engine(self, isolated_db):
        engines = {e["key"]: e["speakers_without_voice"]
                   for e in listen_service.get_listen_config(self._title(isolated_db))["tts_engines"]}
        assert engines == {"omnivoice": [], "qwen3_tts": ["Wei"], "chatterbox": ["Wei"]}

    def test_a_speaker_with_nothing_to_say_is_not_named(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A"), _line(1, "B", en="")])
        engines = listen_service.get_listen_config(did)["tts_engines"]
        assert [e["speakers_without_voice"] for e in engines if e["key"] == "qwen3_tts"] == [["A"]]

    def test_lines_with_no_speaker_are_named_plainly(self, isolated_db):
        did = _drama(isolated_db, [_line(0, None)])
        engines = listen_service.get_listen_config(did)["tts_engines"]
        assert [e["speakers_without_voice"] for e in engines if e["key"] == "chatterbox"] == [["Lines with no speaker"]]

    def test_start_refuses_before_any_job_starts(self, isolated_db, monkeypatch):
        did = self._title(isolated_db)
        started = []
        monkeypatch.setattr(listen_service.job_launch, "start_process_job", lambda *a, **k: started.append(1))
        with pytest.raises(InvalidInputError) as err:
            listen_service.start_listen(did, "qwen3_tts")
        assert str(err.value) == ("Wei has no voice yet. Qwen3-TTS needs a reference clip or a voice description "
                                  "for every speaker; only OmniVoice picks voices on its own.")
        assert started == []

    def test_start_lets_omnivoice_pick_voices(self, isolated_db, monkeypatch):
        did = self._title(isolated_db)
        monkeypatch.setattr(listen_service.job_launch, "start_process_job", lambda *a, **k: True)
        assert listen_service.start_listen(did, "omnivoice") == {"job_id": f"dub_{did}"}


class TestSteadyDefaultVoices:
    """A speaker OmniVoice picks a voice for keeps it: the pick is saved on the character (and
    its series character), so a new speaker in a later chapter doesn't move everyone's voice
    along the list, which would also speak every one of their clips again."""

    @pytest.fixture(autouse=True)
    def _engines_installed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(listen_service, "_engine_install_problem", lambda engine: None)

    @pytest.fixture
    def runs(self, monkeypatch):
        calls = []
        monkeypatch.setattr(listen_service.job_launch, "start_process_job",
                            lambda job_id, worker, args=(), **kw: calls.append((args, kw)) or True)
        return calls

    def _voices(self, db, did):
        return {c["speaker_label"]: c["voice_design"] for c in db.list_characters(did)}

    def test_a_new_speaker_in_a_later_chapter_does_not_move_the_others(self, isolated_db, runs):
        d = dub.DEFAULT_VOICE_DESCRIPTIONS
        did = _drama(isolated_db, [_line(0, "Bob"), _line(1, "Narrator")])
        listen_service.start_listen(did, "omnivoice")
        assert self._voices(isolated_db, did) == {"Bob": d[0], "Narrator": d[1]}

        # "Alice" sorts before both, which used to give her Bob's voice and Bob the Narrator's.
        isolated_db.save_lines(did, [_line(0, "Bob"), _line(1, "Narrator"), _line(2, "Alice")])
        listen_service.start_listen(did, "omnivoice")
        assert self._voices(isolated_db, did) == {"Bob": d[0], "Narrator": d[1], "Alice": d[2]}
        clone_map = runs[-1][0][2]
        assert {k: v["instruct"] for k, v in clone_map.items()} == {"Bob": d[0], "Narrator": d[1], "Alice": d[2]}

    def test_the_series_character_keeps_the_voice_for_the_next_title(self, isolated_db, runs):
        sid = isolated_db.get_or_create_series("Saga")
        wei = isolated_db.insert_series_character(sid, "Wei")
        first = _drama(isolated_db, [_line(0, "Lin"), _line(1, "Wei")], series_id=sid)
        isolated_db.upsert_character(first, "Wei", series_character_id=wei)
        listen_service.start_listen(first, "omnivoice")
        picked = self._voices(isolated_db, first)["Wei"]

        second = _drama(isolated_db, [_line(0, "Ann"), _line(1, "Wei")], series_id=sid)
        isolated_db.upsert_character(second, "Wei", series_character_id=wei)
        listen_service.start_listen(second, "omnivoice")
        voices = self._voices(isolated_db, second)
        assert voices["Wei"] == picked and voices["Ann"] != picked

    def test_a_voice_of_your_own_is_never_overwritten(self, isolated_db, runs):
        did = _drama(isolated_db, [_line(0, "A")])
        isolated_db.upsert_character(did, "A", voice_design="male, low pitch")
        listen_service.start_listen(did, "omnivoice")
        assert self._voices(isolated_db, did) == {"A": "male, low pitch"}

    def test_lines_with_no_speaker_get_a_voice_but_nothing_is_saved_for_them(self, isolated_db, runs):
        did = _drama(isolated_db, [_line(0, None)])
        listen_service.start_listen(did, "omnivoice")
        assert isolated_db.list_characters(did) == []
        assert runs[0][0][2][None]["instruct"] == dub.DEFAULT_VOICE_DESCRIPTIONS[0]

    def test_a_cancelled_run_leaves_no_half_written_track(self, isolated_db, runs):
        did = _drama(isolated_db, [_line(0, "A")])
        listen_service.start_listen(did, "omnivoice")
        part = os.path.join(isolated_db.drama_dir(did), "narration_track.flac.part")
        open(part, "wb").close()
        runs[0][1]["on_finish"](f"dub_{did}")
        assert not os.path.exists(part)


class TestARefusedStartChangesNothing:
    """A start that is refused must leave the title as it was: its Voice language and the
    voices OmniVoice would pick are saved only once the job has really started."""

    @pytest.fixture(autouse=True)
    def _engines_installed(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(listen_service, "_engine_install_problem", lambda engine: None)

    def test_a_speaker_without_a_voice_keeps_the_voice_language(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        with pytest.raises(InvalidInputError):
            listen_service.start_listen(did, "qwen3_tts", narration_language="original")
        assert isolated_db.get_drama(did).get("narration_language") in (None, "", "translation")

    def test_a_start_the_job_table_refuses_saves_neither_language_nor_voices(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, [_line(0, "A")])
        monkeypatch.setattr(listen_service.job_launch, "start_process_job", lambda *a, **k: False)
        with pytest.raises(ConflictError, match="A narration is already running for this title."):
            listen_service.start_listen(did, "omnivoice", narration_language="original")
        assert isolated_db.get_drama(did).get("narration_language") in (None, "", "translation")
        assert isolated_db.list_characters(did) == []

    def test_a_started_run_saves_both(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, [_line(0, "A")])
        monkeypatch.setattr(listen_service.job_launch, "start_process_job", lambda *a, **k: True)
        listen_service.start_listen(did, "omnivoice", narration_language="original")
        assert isolated_db.get_drama(did)["narration_language"] == "original"
        assert [c["voice_design"] for c in isolated_db.list_characters(did)] == [dub.DEFAULT_VOICE_DESCRIPTIONS[0]]


class TestClipTranscriptsWhisperWrote:
    def test_whisper_is_used_only_when_on_this_pc_and_from_the_settings_folder(self, isolated_db, monkeypatch, tmp_path):
        asked = []
        monkeypatch.setattr(listen_service.model_readiness_service, "whisper_ready",
                            lambda size, local=None: asked.append((size, local)) or local is not None)
        monkeypatch.setattr(listen_service.settings_service, "get",
                            lambda key: str(tmp_path) if key == "whisper_model_path" else None)
        assert listen_service._clip_whisper() == {"local_model_path": str(tmp_path)}
        monkeypatch.setattr(listen_service.settings_service, "get", lambda key: None)
        # large-v3 not downloaded and no folder set: no transcript, not a silent download.
        assert listen_service._clip_whisper() is None
        assert asked == [("large-v3", str(tmp_path)), ("large-v3", None)]

    def _speakers(self, db, did):
        for label, clip, text in (("A", "voice_refs/a.wav", ""), ("B", "voice_refs/b.wav", ""),
                                  ("C", "voice_refs/c.wav", "Typed meanwhile.")):
            db.upsert_character(did, label, ref_audio_filename=clip, ref_text=text)
        # B's clip was replaced while the run went.
        db.upsert_character(did, "B", ref_audio_filename="voice_refs/b2.wav")

    def test_saved_only_where_the_clip_is_the_same_and_no_transcript_was_typed(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        self._speakers(isolated_db, did)
        at_start = {"A": "voice_refs/a.wav", "B": "voice_refs/b.wav", "C": "voice_refs/c.wav"}
        listen_service.apply_narration_result(
            did, {"lines": [], "errors": [], "clip_transcripts": {"A": "你好。", "B": "别的。", "C": "听到的。"}},
            at_start)
        texts = {c["speaker_label"]: c["ref_text"] for c in isolated_db.list_characters(did)}
        assert texts == {"A": "你好。", "B": "", "C": "Typed meanwhile."}


class TestUnusedClipsAfterARun:
    """Each voice or text change left the old clips behind (about 170 MB per narrated hour).
    After a run's lines are saved, only clips no line of the title names are deleted, and
    only the clip files the narration itself writes."""

    def _clip(self, db, did, name):
        folder = os.path.join(db.drama_dir(did), "dub_clips")
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, name), "wb").close()
        return name

    def _left(self, db, did):
        return sorted(os.listdir(os.path.join(db.drama_dir(did), "dub_clips")))

    def _produced(self, db, did, names):
        out = []
        for ln in db.load_line_objects(did):
            if ln.idx in names:
                ln.dub_filename = os.path.join("dub_clips", names[ln.idx])
                out.append(ln)
        return out

    def test_old_clips_go_and_clips_any_line_names_stay(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A"), _line(1, "A"), _line(2, "B", en="")])
        new0 = self._clip(isolated_db, did, "line_0000_aaaaaaaaaa.wav")
        old0 = self._clip(isolated_db, did, "line_0000_bbbbbbbbbb.wav")
        joined = self._clip(isolated_db, did, "line_0001-0002_cccccccccc.wav")
        mine = [self._clip(isolated_db, did, n) for n in (
            "voice_spec_1.json", "line_0003_dddddddddd.partial.wav", "notes.wav", "line_0004_DDDDDDDDDD.wav")]
        # Line 2 is not in this run but still names a clip from an earlier one.
        lines = isolated_db.load_line_objects(did)
        lines[2].dub_filename = os.path.join("dub_clips", joined)
        isolated_db.save_lines(did, lines, fields=("dub_filename",))
        listen_service.apply_narration_result(did, {"lines": self._produced(isolated_db, did, {0: new0, 1: new0}),
                                                    "errors": []})
        assert self._left(isolated_db, did) == sorted([new0, joined, *mine])
        assert old0 not in self._left(isolated_db, did)

    def test_nothing_is_deleted_when_nothing_was_narrated(self, isolated_db):
        did = _drama(isolated_db, [_line(0, "A")])
        old = self._clip(isolated_db, did, "line_0000_bbbbbbbbbb.wav")
        produced = self._produced(isolated_db, did, {})
        for ln in isolated_db.load_line_objects(did):
            produced.append(ln)
        listen_service.apply_narration_result(did, {"lines": produced, "errors": [{"line_idx": 0, "error": "x"}]})
        assert self._left(isolated_db, did) == [old]

    def test_a_failed_save_deletes_nothing(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, [_line(0, "A")])
        old = self._clip(isolated_db, did, "line_0000_bbbbbbbbbb.wav")
        new = self._clip(isolated_db, did, "line_0000_aaaaaaaaaa.wav")

        def refuse(*a, **k):
            raise ConflictError("changed")
        monkeypatch.setattr(isolated_db, "save_lines", refuse)
        with pytest.raises(ConflictError):
            listen_service.apply_narration_result(did, {"lines": self._produced(isolated_db, did, {0: new}),
                                                        "errors": []})
        assert self._left(isolated_db, did) == sorted([old, new])

    def test_a_clip_that_cannot_be_deleted_does_not_fail_the_run(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, [_line(0, "A")])
        self._clip(isolated_db, did, "line_0000_bbbbbbbbbb.wav")
        new = self._clip(isolated_db, did, "line_0000_aaaaaaaaaa.wav")

        def held(path):
            raise PermissionError(13, "in use")
        monkeypatch.setattr(os, "remove", held)
        listen_service.apply_narration_result(did, {"lines": self._produced(isolated_db, did, {0: new}),
                                                    "errors": []})
        assert isolated_db.get_drama(did)["status"] == "dubbed"


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

    def test_omnivoice_counts_as_installed_only_when_its_venv_is_ready(self, monkeypatch, tmp_path):
        from voice import voice_venv
        monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
        # Its package is never importable in the app's own environment.
        self._without(monkeypatch, "omnivoice")
        assert listen_service._missing_engine_dependency("omnivoice") == (
            "OmniVoice is not installed. Install it in Diagnostics.")
        monkeypatch.setattr(voice_venv, "is_ready", lambda engine: engine == "omnivoice")
        assert listen_service._missing_engine_dependency("omnivoice") is None

    def test_qwen3_tts_counts_as_installed_only_when_its_venv_is_ready(self, monkeypatch,
                                                                        tmp_path):
        from voice import voice_venv
        monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
        # Its package is never importable in the app's own environment.
        self._without(monkeypatch, "qwen_tts")
        assert listen_service._missing_engine_dependency("qwen3_tts") == (
            "Qwen3-TTS is not installed. Install it in Diagnostics.")
        monkeypatch.setattr(voice_venv, "is_ready", lambda engine: engine == "qwen3_tts")
        assert listen_service._missing_engine_dependency("qwen3_tts") is None

    def test_no_message_names_a_path_or_the_server_address(self, monkeypatch):
        monkeypatch.setenv("BAIHE_GPT_SOVITS_URL", FAKE_URL)
        for engine in listen_service.dub.CLONE_ENGINES:
            assert FAKE_URL not in (listen_service._missing_engine_dependency(engine) or "")


class TestUpgradeFromTheDubStage:
    """A library saved while the Dub stage existed keeps working: its old
    settings and job records are ignored, and Listen reads the title."""

    def test_old_dub_leftovers_do_not_stop_startup_or_listen(self, isolated_db):
        from services import jobs_service
        did = _drama(isolated_db, [_line(0, speaker="S1")], status="dubbed")
        isolated_db.set_app_setting("voice.dub.pacing", {"max_speedup": 1.3})
        isolated_db.set_app_setting("dub_keep_background", True)
        isolated_db.set_app_setting("storage.dub_bitrate", "192k")
        isolated_db.save_job_record(f"dub_{did}", status="done", progress=1.0)
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "dub_track.wav"), "wb").close()

        isolated_db.init_db()   # what a restart runs over the saved library

        config = listen_service.get_listen_config(did)
        assert config["track_available"] is False
        assert jobs_service.get_job(f"dub_{did}")["status"] == "done"
        assert isolated_db.get_app_setting("voice.dub.pacing") == {"max_speedup": 1.3}


class TestPydubCheck:
    def test_missing_pydub_is_named_with_the_fix_and_blocks_every_engine(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        import importlib.util
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "pydub" else real(n, *a))
        monkeypatch.setattr(listen_service, "_pydub_problem", listen_service._pydub_problem.real)
        reason = listen_service._missing_engine_dependency("omnivoice")
        assert "pydub" in reason and "Diagnostics" in reason and "No module" not in reason
