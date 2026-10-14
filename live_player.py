"""
live_player.py -- the rolling copy of a Live stream that the page plays
instead of the YouTube embed, so the picture can be held back to match the
captions on a stream the embed cannot rewind.

Two recorders, each a live_fetch.StreamPump feeding one ffmpeg that copies
(video) or re-encodes (audio, a few kbit/s) into a rolling HLS playlist in
the session folder: video.m3u8 and audio.m3u8. The page's player joins them
through the master.m3u8 written here. They are separate from the caption
capture, which stays exactly as it was, and, like it, ffmpeg opens nothing
itself (`-protocol_whitelist pipe`): every fetch goes through the pump and the
guarded proxy.

The two source streams stamp their packets on different clocks (a video
stream's first packet can read 2527 s where the audio's reads 1.4 s), so their
own timestamps cannot be used to line them up. Each ffmpeg instead restarts its
clock at its first packet, and live_fetch.SequenceAlign makes both pumps begin
at the same media sequence number, which on a broadcast's playlists is the same moment.
"""
import os
import re
import subprocess

import background_jobs
import live_fetch

SEGMENT_SECONDS = 4
# The page can delay up to 90 s; the rest is slack for the player to start behind.
LIST_SIZE = 40
VIDEO_HEIGHT_CAP = 480
# MPEG-TS holds H.264 but not VP9 or AV1, which a plain "best video" may pick.
VIDEO_LADDER = [(f"bestvideo[height<={VIDEO_HEIGHT_CAP}][vcodec^=avc1]/bestvideo[vcodec^=avc1]", None)]
PLAYER_DIR = "player"

MASTER = "master.m3u8"
VIDEO_PLAYLIST = "video.m3u8"
AUDIO_PLAYLIST = "audio.m3u8"
# The only names the route serves: fixed playlists and numbered segments.
SERVED_NAME = re.compile(r"^(?:master|video|audio)\.m3u8$|^[va]_\d{6}\.ts$")

_MASTER_TEXT = (
    "#EXTM3U\n"
    '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="%s"\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=2000000,AUDIO="a"\n'
    "%s\n") % (AUDIO_PLAYLIST, VIDEO_PLAYLIST)


class Recorder:
    """One ffmpeg and the pump feeding it; stop it with live_translate.stop_capture,
    which reads these two attributes."""

    def __init__(self, proc: subprocess.Popen, pump: live_fetch.StreamPump):
        self.proc = proc
        self.pump = pump

    @property
    def error(self):
        return self.pump.error


def player_dir(out_dir: str) -> str:
    return os.path.join(out_dir, PLAYER_DIR)


def ffmpeg_cmd(kind: str, folder: str, whitelist: str) -> list:
    """The ffmpeg command for "video" or "audio" writing into `folder`."""
    tracks = ["-an", "-c:v", "copy"] if kind == "video" else ["-vn", "-c:a", "aac", "-b:a", "128k"]
    prefix = "v" if kind == "video" else "a"
    playlist = VIDEO_PLAYLIST if kind == "video" else AUDIO_PLAYLIST
    return ["ffmpeg", "-y", "-loglevel", "error", "-protocol_whitelist", "pipe",
            "-format_whitelist", whitelist, "-i", "pipe:0", *tracks,
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", str(LIST_SIZE),
            "-hls_flags", "delete_segments",
            "-hls_segment_filename", os.path.join(folder, f"{prefix}_%06d.ts"),
            os.path.join(folder, playlist)]


def start_recorder(kind: str, source_url: str, folder: str, proxy: str = None, align=None) -> Recorder:
    from live_translate import FFMPEG_FORMAT_WHITELIST
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(ffmpeg_cmd(kind, folder, FFMPEG_FORMAT_WHITELIST), stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **group)
    try:
        pump = live_fetch.StreamPump(source_url, proc.stdin, proxy=proxy, align=align).start()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return Recorder(proc, pump)


class PlayerRecorders:
    """The video and audio recorders of one session."""

    def __init__(self, folder: str, recorders: list):
        self.folder = folder
        self.recorders = recorders

    def failure(self):
        """A fixed message once either recorder has failed, else None."""
        for rec in self.recorders:
            if rec.error:
                return rec.error
            if rec.proc.poll() not in (None, 0):
                return "The delayed player's recorder stopped."
        return None

    def stop(self, kill: bool = False):
        from live_translate import stop_capture
        for rec in self.recorders:
            stop_capture(rec, kill=kill)


def start_player(video_url: str, audio_url: str, out_dir: str, proxy: str = None) -> PlayerRecorders:
    folder = player_dir(out_dir)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MASTER), "w", encoding="ascii") as fh:
        fh.write(_MASTER_TEXT)
    started = []
    align = live_fetch.SequenceAlign()
    try:
        started.append(start_recorder("video", video_url, folder, proxy, align))
        started.append(start_recorder("audio", audio_url, folder, proxy, align))
    except BaseException:
        for rec in started:
            background_jobs.kill_tree(rec.proc)
            rec.pump.halt()
        raise
    return PlayerRecorders(folder, started)


def served_file(out_dir: str, name: str):
    """The path of a file the player may fetch, or None: a fixed playlist or a
    numbered segment inside this session's player folder, and nothing else."""
    if not isinstance(name, str) or not SERVED_NAME.fullmatch(name):
        return None
    folder = os.path.realpath(player_dir(out_dir))
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.dirname(path) != folder or not os.path.isfile(path):
        return None
    return path


UNAVAILABLE_NOTE = "Delayed player unavailable: no separate video stream could be recorded."
STOPPED_NOTE = "The delayed player stopped; captions continue."


def start_if_possible(enabled, url, audio_url, out_dir, note, should_stop, stream_url_check=None, proxy=None,
                      cookies_browser=None, cookies_file=None):
    """The recorders for this session. None when `enabled` is off, or, with a fixed note, when the
    video stream can't be resolved or started. Never raises: the captions do not depend on it.
    A Stop interrupts the lookup (`should_stop`)."""
    if not enabled:
        return None
    from live_translate import _run_abortable, resolve_stream_url
    try:
        video_url = _run_abortable(lambda: resolve_stream_url(
            url, cookies_browser=cookies_browser, cookies_file=cookies_file, proxy=proxy,
            ladder=VIDEO_LADDER), should_stop)
        if stream_url_check is not None:
            stream_url_check(video_url)
        return start_player(video_url, audio_url, out_dir, proxy=proxy)
    except background_jobs.JobCancelled:
        raise
    except Exception:
        note(UNAVAILABLE_NOTE, "player")
        return None


def ready(out_dir) -> bool:
    """True once both playlists list at least one segment, so the page can start playing."""
    if not out_dir:
        return False
    for name in (VIDEO_PLAYLIST, AUDIO_PLAYLIST):
        path = served_file(out_dir, name)
        try:
            with open(path, encoding="ascii", errors="replace") as fh:
                if "#EXTINF" not in fh.read(65536):
                    return False
        except (OSError, TypeError):
            return False
    return True


def watch(player, note):
    """`player` while its recorders run; after one fails, stops both, says so and returns None."""
    if player is not None and player.failure():
        player.stop(kill=True)
        note(STOPPED_NOTE, "player")
        return None
    return player
