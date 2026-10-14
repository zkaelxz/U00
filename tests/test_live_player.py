"""
tests/test_live_player.py -- the delayed player's recorder commands, the files the
route may serve, and when the player counts as ready. One test runs the real ffmpeg
command on a generated stream to prove it writes a rolling playlist.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
import live_player
from live_translate import FFMPEG_FORMAT_WHITELIST
from services import live_service
from lib.errors import NotFoundError


def test_commands_read_only_from_the_pipe_and_keep_source_timestamps():
    for kind, flag in (("video", "-an"), ("audio", "-vn")):
        cmd = live_player.ffmpeg_cmd(kind, "/tmp/p", FFMPEG_FORMAT_WHITELIST)
        assert cmd[cmd.index("-protocol_whitelist") + 1] == "pipe"
        assert cmd[cmd.index("-i") + 1] == "pipe:0"
        assert "-copyts" in cmd and flag in cmd
    assert "copy" in live_player.ffmpeg_cmd("video", "/tmp/p", "mpegts")
    assert "aac" in live_player.ffmpeg_cmd("audio", "/tmp/p", "mpegts")


@pytest.fixture
def folder(tmp_path):
    out = tmp_path / "session"
    (out / live_player.PLAYER_DIR).mkdir(parents=True)
    return out


def _write(out, name, text="x"):
    path = out / live_player.PLAYER_DIR / name
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize("name", ["master.m3u8", "video.m3u8", "audio.m3u8", "v_000001.ts", "a_000123.ts"])
def test_serves_only_fixed_names(folder, name):
    path = _write(folder, name)
    assert live_player.served_file(str(folder), name) == os.path.realpath(path)


@pytest.mark.parametrize("name", [
    "../secret.m3u8", "..%2fx.ts", "v_1.ts", "v_000001.ts.bak", "other.m3u8", "chunk_00000.wav",
    "video.m3u8/", "/etc/passwd", "v_000001.TS", "", None])
def test_refuses_every_other_name(folder, name):
    _write(folder, "video.m3u8")
    (folder / "chunk_00000.wav").write_text("audio")
    assert live_player.served_file(str(folder), name) is None


def test_refuses_a_missing_file_and_a_symlink_out_of_the_folder(folder, tmp_path):
    assert live_player.served_file(str(folder), "v_000009.ts") is None
    outside = tmp_path / "outside.ts"
    outside.write_text("secret")
    link = folder / live_player.PLAYER_DIR / "v_000002.ts"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available")
    assert live_player.served_file(str(folder), "v_000002.ts") is None


def test_ready_needs_both_playlists_to_list_a_segment(folder):
    assert not live_player.ready(str(folder)) and not live_player.ready(None)
    _write(folder, "video.m3u8", "#EXTM3U\n#EXTINF:4.0,\nv_000000.ts\n")
    assert not live_player.ready(str(folder))
    _write(folder, "audio.m3u8", "#EXTM3U\n")
    assert not live_player.ready(str(folder))
    _write(folder, "audio.m3u8", "#EXTM3U\n#EXTINF:4.0,\na_000000.ts\n")
    assert live_player.ready(str(folder))


def test_master_playlist_joins_the_audio_rendition_to_the_video(tmp_path):
    class Rec:
        def __init__(self):
            self.proc = None
            self.pump = None
    started = []
    orig = live_player.start_recorder
    live_player.start_recorder = lambda kind, url, folder, proxy=None: started.append(kind) or Rec()
    try:
        live_player.start_player("v", "a", str(tmp_path))
    finally:
        live_player.start_recorder = orig
    text = (tmp_path / live_player.PLAYER_DIR / "master.m3u8").read_text()
    assert started == ["video", "audio"]
    assert 'URI="audio.m3u8"' in text and "video.m3u8" in text and 'AUDIO="a"' in text


def test_start_if_possible_reports_a_fixed_note_and_returns_none(tmp_path, monkeypatch):
    import live_translate
    notes = []

    def boom(*a, **k):
        raise RuntimeError("https://secret.example/stream?token=abc")
    monkeypatch.setattr(live_translate, "resolve_stream_url", boom)
    got = live_player.start_if_possible(True, "https://x.test", "a", str(tmp_path),
                                        lambda text, key=None: notes.append((text, key)),
                                        lambda: False)
    assert got is None
    assert notes == [(live_player.UNAVAILABLE_NOTE, "player")]
    assert "secret" not in notes[0][0]


def test_a_cancel_while_resolving_is_not_swallowed(tmp_path, monkeypatch):
    import live_translate

    def cancelled(*a, **k):
        raise background_jobs.JobCancelled("x")
    monkeypatch.setattr(live_translate, "resolve_stream_url", cancelled)
    with pytest.raises(background_jobs.JobCancelled):
        live_player.start_if_possible(True, "https://x.test", "a", str(tmp_path), lambda *a: None, lambda: False)


def test_player_file_is_a_404_for_an_unknown_session_and_other_names(folder, monkeypatch):
    with pytest.raises(NotFoundError):
        live_service.player_file("live_" + "a" * 32, "video.m3u8")
    monkeypatch.setitem(live_service._sessions, "live_" + "b" * 32,
                        {"dir": str(folder), "owner_user_id": None})
    monkeypatch.setattr(live_service, "_require", lambda *a, **k: {})
    _write(folder, "video.m3u8")
    assert live_service.player_file("live_" + "b" * 32, "video.m3u8").endswith("video.m3u8")
    with pytest.raises(NotFoundError):
        live_service.player_file("live_" + "b" * 32, "../x.ts")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("kind", ["video", "audio"])
def test_real_ffmpeg_writes_a_rolling_playlist(tmp_path, kind):
    source = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=9" if kind == "video"
              else "sine=frequency=440:duration=9"]
    codec = (["-c:v", "libx264", "-preset", "ultrafast"] if kind == "video" else ["-c:a", "aac"])
    stream = subprocess.run(["ffmpeg", "-loglevel", "error", *source, *codec, "-f", "mpegts", "pipe:1"],
                            capture_output=True, check=True, timeout=60).stdout
    folder = tmp_path / "player"
    folder.mkdir()
    cmd = live_player.ffmpeg_cmd(kind, str(folder), FFMPEG_FORMAT_WHITELIST)
    subprocess.run(cmd, input=stream, capture_output=True, check=True, timeout=60)
    playlist = (folder / f"{kind}.m3u8").read_text()
    assert "#EXTINF" in playlist
    assert (folder / f"{kind[0]}_000000.ts").exists()


def test_watch_stops_a_failed_player_and_says_so():
    stopped, notes = [], []

    class Failed:
        def failure(self):
            return "x"

        def stop(self, kill=False):
            stopped.append(kill)

    class Fine:
        def failure(self):
            return None
    fine = Fine()
    assert live_player.watch(fine, lambda *a: notes.append(a)) is fine and live_player.watch(None, None) is None
    assert live_player.watch(Failed(), lambda *a: notes.append(a)) is None
    assert stopped == [True] and notes == [(live_player.STOPPED_NOTE, "player")]
