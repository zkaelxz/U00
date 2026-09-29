"""
media_inspect.py -- Step 59: a lightweight "what is this file?" probe for a
media file dropped before a drama/project even exists (tabs/workspace_tab.py's
New Drama flow calls into this). Pure ffprobe + filename/duration heuristics,
no model loading -- deliberately cheap enough to run on every file a user is
just considering, not only ones they've already committed to.

The content-type guess and suggested pipeline are advisory only, shown to the
user before anything is applied -- this module never writes to the database
or touches session state; the caller (workspace_tab.py) decides what, if
anything, to do with the result.
"""
import dataclasses
import json
import os
import subprocess


class ProbeError(Exception):
    """ffprobe failed, isn't installed, or the file isn't one it can read."""


@dataclasses.dataclass
class AudioTrack:
    index: int
    codec: str
    channels: int | None
    language: str | None


@dataclasses.dataclass
class SubtitleTrack:
    index: int
    codec: str
    language: str | None


@dataclasses.dataclass
class MediaAnalysis:
    duration_seconds: float
    has_video: bool
    width: int | None
    height: int | None
    fps: float | None
    audio_tracks: list
    subtitle_tracks: list
    content_type_guess: str
    content_type_reason: str
    suggested_pipeline: list


# Keyword heuristics only -- no visual/audio content analysis. Values must
# stay in sync with tabs/workspace_tab.py's own MEDIA_TYPE_OPTIONS; this
# module can't import that tab (workspace_tab is the caller here, not the
# other way around), so the overlap is duplicated rather than shared.
_STREAMER_KEYWORDS = ("vtuber", "stream", "live", "vod", "broadcast")
_ASMR_KEYWORDS = ("asmr", "binaural", "roleplay", " rp ", "-rp-")


def run_ffprobe(path: str, timeout: int = 30) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe isn't installed, or isn't on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout}s.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or str(exc)
        raise ProbeError(f"ffprobe couldn't read this file: {detail}") from exc
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned output that isn't valid JSON.") from exc


def _parse_fps(rate: str):
    # ffprobe always reports r_frame_rate as "num/den" (e.g. "30000/1001"),
    # never a plain float.
    if not rate or rate == "0/0":
        return None
    try:
        num, den = rate.split("/")
        den = float(den)
        return float(num) / den if den else None
    except ValueError:
        return None


def probe_media(path: str, filename: str | None = None, timeout: int = 30) -> MediaAnalysis:
    """Analyze a dropped media file: real duration/resolution/fps/track data
    via ffprobe, plus an advisory content-type guess and suggested pipeline.

    `filename` is the original name the user gave the file -- pass it
    whenever `path` is a temp file, since the filename-keyword part of the
    content-type guess needs the real name, not a generated temp one.
    """
    probe = run_ffprobe(path, timeout=timeout)
    fmt = probe.get("format", {}) or {}
    duration = float(fmt.get("duration", 0.0) or 0.0)

    has_video = False
    width = height = fps = None
    audio_tracks = []
    subtitle_tracks = []
    for stream in probe.get("streams", []):
        codec_type = stream.get("codec_type")
        tags = stream.get("tags", {}) or {}
        language = tags.get("language")
        if language in ("und", "unk", ""):
            language = None
        if codec_type == "video":
            # A single embedded cover-art image (common in .mp3/.m4a files)
            # shows up as its own "video" stream -- exclude it, or an
            # audio-only file would get wrongly reported as having video.
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue
            has_video = True
            width = stream.get("width")
            height = stream.get("height")
            fps = _parse_fps(stream.get("r_frame_rate"))
        elif codec_type == "audio":
            audio_tracks.append(AudioTrack(
                index=stream.get("index"), codec=stream.get("codec_name", "?"),
                channels=stream.get("channels"), language=language,
            ))
        elif codec_type == "subtitle":
            subtitle_tracks.append(SubtitleTrack(
                index=stream.get("index"), codec=stream.get("codec_name", "?"),
                language=language,
            ))

    content_type_guess, content_type_reason = _guess_content_type(
        filename or os.path.basename(path), has_video, duration, audio_tracks)
    suggested_pipeline = _suggest_pipeline(content_type_guess, subtitle_tracks)

    return MediaAnalysis(
        duration_seconds=duration, has_video=has_video, width=width, height=height, fps=fps,
        audio_tracks=audio_tracks, subtitle_tracks=subtitle_tracks,
        content_type_guess=content_type_guess, content_type_reason=content_type_reason,
        suggested_pipeline=suggested_pipeline,
    )


def _guess_content_type(filename: str, has_video: bool, duration: float, audio_tracks: list) -> tuple:
    """Best-effort only -- filename keywords and coarse duration/track shape,
    never visual/audio content analysis. Returns one of workspace_tab.py's
    own MEDIA_TYPE_OPTIONS values, plus a one-line reason to show the user
    (never applied silently -- see the caller in workspace_tab.py)."""
    name = f" {filename.lower()} "
    if any(kw in name for kw in _STREAMER_KEYWORDS):
        return "streamer_vod", "filename suggests a stream recording/VOD"
    if not has_video:
        if any(kw in name for kw in _ASMR_KEYWORDS):
            return "asmr", "audio-only file with an ASMR-style filename"
        if len(audio_tracks) <= 1 and duration > 20 * 60:
            return "audio_drama", "long, single-track audio file"
        return "audio_drama", "audio-only file"
    return "video_drama", "has a video track"


def _suggest_pipeline(content_type_guess: str, subtitle_tracks: list) -> list:
    """A suggested, not applied, pipeline -- matches this app's existing
    'show the plan before executing' pattern (see e.g. sources_tab.py's
    front-door preview, or workspace_tab.py's preview/apply pairs)."""
    steps = []
    if subtitle_tracks:
        langs = ", ".join(t.language or "unknown" for t in subtitle_tracks)
        steps.append(f"Import existing subtitle track ({langs}) instead of transcribing")
    else:
        steps.append("Transcribe (Whisper)")
        if content_type_guess != "asmr":
            steps.append("Diarize speakers")
    steps.append("Translate")
    steps.append("Export subtitles (ASS/VTT/SRT)")
    return steps
