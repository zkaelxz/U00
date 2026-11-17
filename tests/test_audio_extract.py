"""Tests for audio_extract.sndfile_readable: non-wav/flac audio is decoded before soundfile reads it."""
import os

import audio_extract


def test_wav_and_flac_are_read_in_place(isolated_db):
    for name in ("/x/audio.wav", "/x/Audio.FLAC"):
        with audio_extract.sndfile_readable(name) as path:
            assert path == name


def test_m4a_is_decoded_to_a_temporary_wav_that_is_removed(isolated_db, monkeypatch):
    seen = {}

    def fake_extract(src, out):
        seen["src"], seen["out"] = src, out
        with open(out, "wb") as f:
            f.write(b"RIFF")
        return out
    monkeypatch.setattr(audio_extract, "extract_audio_from_video", fake_extract)
    with audio_extract.sndfile_readable("/x/source.m4a") as path:
        assert path == seen["out"] and path.endswith(".wav")
        assert seen["src"] == "/x/source.m4a"
        assert os.path.exists(path)
    assert not os.path.exists(seen["out"])
