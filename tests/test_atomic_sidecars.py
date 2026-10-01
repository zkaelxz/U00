"""Sidecar files (diarization turns, audio tags, CEDICT) are written atomically."""
import gzip
import io
import os

import pytest

import core
import dictionary
import diarize
import sensevoice_tags


def _fail_replace(*a, **k):
    raise OSError("boom")


def test_save_turns_failure_keeps_old_file(tmp_path, monkeypatch):
    diarize.save_turns(str(tmp_path), [{"start": 0, "end": 1, "speaker": "A"}])
    with monkeypatch.context() as m:
        m.setattr(core.os, "replace", _fail_replace)
        with pytest.raises(OSError):
            diarize.save_turns(str(tmp_path), [{"start": 5, "end": 6, "speaker": "B"}])
    assert diarize.load_turns(str(tmp_path))[0]["speaker"] == "A"
    assert os.listdir(tmp_path) == [diarize.TURNS_FILE]


def test_corrupt_turns_file_treated_as_missing(tmp_path):
    (tmp_path / diarize.TURNS_FILE).write_text('{"turns": [', encoding="utf-8")
    assert diarize.load_turns(str(tmp_path)) is None
    assert diarize.load_last_speaker_count(str(tmp_path)) is None
    assert diarize.load_embeddings(str(tmp_path)) == {}
    assert diarize.load_last_run_info(str(tmp_path))["device"] is None


def test_save_audio_tags_failure_keeps_old_file(tmp_path, monkeypatch):
    sensevoice_tags.save_audio_tags(str(tmp_path), {1: {"emotion": "happy"}})
    with monkeypatch.context() as m:
        m.setattr(core.os, "replace", _fail_replace)
        with pytest.raises(OSError):
            sensevoice_tags.save_audio_tags(str(tmp_path), {2: {"emotion": "sad"}})
    assert sensevoice_tags.load_audio_tags(str(tmp_path)) == {1: {"emotion": "happy"}}
    assert os.listdir(tmp_path) == [sensevoice_tags.AUDIO_TAGS_FILE]


def test_corrupt_audio_tags_treated_as_missing(tmp_path):
    (tmp_path / sensevoice_tags.AUDIO_TAGS_FILE).write_text("{trunc", encoding="utf-8")
    assert sensevoice_tags.load_audio_tags(str(tmp_path)) == {}


def test_cedict_write_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    path = str(tmp_path / "library" / "cedict.txt")
    monkeypatch.setattr(dictionary, "CEDICT_PATH", path)

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dictionary.urllib.request, "urlopen",
                        lambda *a, **k: Resp(gzip.compress(b"# x\n")))
    monkeypatch.setattr(core.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        dictionary._ensure_cedict()
    assert os.listdir(os.path.dirname(path)) == []
