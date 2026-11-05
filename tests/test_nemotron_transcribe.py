"""tools/nemotron_transcribe.py: loopback-only server, word grouping, SRT text, overwrite refusal. No server, no model."""
import json

import pytest

from tools import nemotron_transcribe as nt


def test_only_loopback_servers_are_accepted():
    assert nt.check_server("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"
    assert nt.check_server("http://localhost:9000") == "http://localhost:9000"
    for url in ("http://192.168.1.5:8080", "http://example.com", "file:///etc/passwd", "ftp://127.0.0.1"):
        with pytest.raises(ValueError):
            nt.check_server(url)


def test_words_split_on_pause_speaker_change_and_length():
    words = [
        {"word": "你", "start": 0.0, "end": 0.2, "speaker": "S0"},
        {"word": "好", "start": 0.2, "end": 0.4, "speaker": "S0"},
        {"word": "嗯", "start": 1.6, "end": 1.8, "speaker": "S0"},     # pause of 1.2 s
        {"word": "是", "start": 1.8, "end": 2.0, "speaker": "S1"},     # speaker change
        {"word": " ", "start": 2.0, "end": 2.1, "speaker": "S1"},      # blank word is dropped
    ]
    lines = nt.group_words(words, "zh", offset=10.0)
    assert [(ln["text"], ln["speaker"]) for ln in lines] == [("你好", "S0"), ("嗯", "S0"), ("是", "S1")]
    assert lines[0]["start"] == pytest.approx(10.0) and lines[0]["end"] == pytest.approx(10.4)

    long_run = [{"word": "字", "start": i * 0.1, "end": i * 0.1 + 0.1} for i in range(70)]
    assert [len(ln["text"]) for ln in nt.group_words(long_run, "ja")] == [30, 30, 10]


def test_spaced_languages_join_with_spaces():
    words = [{"word": "hello", "start": 0, "end": 0.3}, {"word": "world", "start": 0.3, "end": 0.6}]
    assert nt.group_words(words, "en")[0]["text"] == "hello world"


def test_srt_text_has_times_and_speaker_labels():
    srt = nt.to_srt([{"start": 3661.5, "end": 3662.25, "text": "你好", "speaker": "S1"},
                     {"start": 0.0, "end": 1.0, "text": "嗯", "speaker": None}])
    assert srt.startswith("1\n01:01:01,500 --> 01:01:02,250\n[S1] 你好\n")
    assert "2\n00:00:00,000 --> 00:00:01,000\n嗯\n" in srt


def test_main_refuses_a_remote_server_and_an_existing_output(tmp_path, capsys):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    assert nt.main([str(audio), "--server", "http://10.0.0.2:8080"]) == 2
    out = tmp_path / "a.nemotron.srt"
    out.write_text("keep", encoding="utf-8")
    assert nt.main([str(audio)]) == 2
    assert out.read_text(encoding="utf-8") == "keep"


def test_main_writes_the_srt_from_the_servers_words(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    seen = {}
    monkeypatch.setattr(nt, "cut_wav", lambda src, dest, start, end: seen.update(cut=(start, end)))

    def fake_transcribe(server, wav, language, diarize):
        seen["call"] = (server, language, diarize)
        return {"language": "zh", "words": [{"word": "你好", "start": 0.0, "end": 0.5}]}

    monkeypatch.setattr(nt, "transcribe", fake_transcribe)
    assert nt.main([str(audio), "--language", "zh", "--start", "5", "--end", "20", "--diarize"]) == 0
    assert seen["cut"] == (5.0, 20.0) and seen["call"] == ("http://127.0.0.1:8080", "zh", True)
    assert "00:00:05,000 --> 00:00:05,500\n你好" in (tmp_path / "a.nemotron.srt").read_text(encoding="utf-8")


def test_multipart_carries_fields_and_the_file():
    body, content_type = nt.multipart({"language": "zh"}, "file", "clip.wav", b"RIFFdata")
    boundary = content_type.split("boundary=")[1]
    assert f"--{boundary}--".encode() in body
    assert b'name="language"\r\n\r\nzh' in body and b'filename="clip.wav"' in body and b"RIFFdata" in body
    json.dumps({"ok": True})  # the module imports json for the reply; keep the dependency exercised
