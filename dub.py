"""
dub.py -- generates an English AI dub track from your translated lines.

Default engine: edge-tts (Microsoft, free, no cloning -- picks from a
fixed voice list). Assign a different TTS voice per character (via the
`characters` table) for a multi-voice cast.

VOICE CLONING (matching the original actors' actual voices) IS wired up,
via F5-TTS (local, GPU recommended -- see synthesize_line_cloned() and
_get_f5tts_model() below) or ElevenLabs (hosted, no GPU needed -- see
clone_voice_elevenlabs()/synthesize_line_elevenlabs()). Reference clips
can be auto-extracted per speaker from the original audio
(extract_reference_clips()) or set manually; build_dub_track() and
build_narration_track() both take a character_clone_map and use
whichever backend a character has configured, falling back to the plain
TTS engine only for characters with no clone reference set. Wired into
the UI at Workspace section 6 (extract/set reference clips) and section
8 (dub generation itself).
"""

import os
import asyncio

# A reasonable default spread of edge-tts English voices for a multi-character cast.
DEFAULT_VOICE_POOL = [
    "en-US-AvaNeural", "en-US-EmmaNeural", "en-US-JennyNeural",
    "en-GB-SoniaNeural", "en-AU-NatashaNeural", "en-US-AriaNeural",
]


class EdgeTTSBlockedError(RuntimeError):
    """Raised when Microsoft rejects an edge-tts request with a 403 on
    the WebSocket handshake -- a known, periodic block (latest reported
    January 2026), not something wrong with the text or voice. Usually
    fixed by `pip install -U edge-tts`; build_dub_track/build_narration_track
    fall back to Piper automatically when it's installed rather than
    losing the line."""


async def _edge_tts_synthesize(text: str, voice: str, out_path: str, rate: str = "+0%"):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    try:
        await communicate.save(out_path)
    except Exception as e:
        # String match rather than a specific exception class: edge-tts
        # wraps the underlying websockets error, and which exact class
        # that is has changed across edge-tts/websockets versions. "403"
        # is the one thing that's stayed constant in every report of this.
        if "403" in str(e):
            raise EdgeTTSBlockedError(
                "Microsoft blocked the request -- run `pip install -U edge-tts`") from e
        raise


def synthesize_line(text: str, voice: str, out_path: str, rate: str = "+0%"):
    """Single hook point: swap this out for a voice-cloning backend later."""
    asyncio.run(_edge_tts_synthesize(text, voice, out_path, rate))


# ---------------------------------------------------------------------------
# Offline TTS (Piper) -- fully local, no internet, no API cost
# ---------------------------------------------------------------------------

_piper_voices = {}

# A few good default Piper English voices (download once, reused after).
# Full catalog: https://github.com/rhasspy/piper/blob/master/VOICES.md
DEFAULT_OFFLINE_VOICE_POOL = [
    "en_US-amy-medium", "en_US-lessac-medium", "en_GB-alba-medium",
    "en_US-kristin-medium", "en_GB-jenny_dioco-medium",
]


def synthesize_line_offline(text: str, voice: str, out_path: str):
    """Requires `pip install piper-tts`. First use of a given voice
    downloads its model file (needs internet once); after that it's
    fully offline. No per-line API cost, works without any network."""
    from piper import PiperVoice
    if voice not in _piper_voices:
        _piper_voices[voice] = PiperVoice.load(voice)
    pv = _piper_voices[voice]
    with open(out_path, "wb") as f:
        pv.synthesize(text, f)
    return out_path


def _synthesize_edge_tts_with_piper_fallback(text: str, voice: str, out_path: str,
                                              character_voice_map: dict, speaker, rate: str = "+0%"):
    """Tries edge-tts; if Microsoft blocks the request (EdgeTTSBlockedError),
    falls back to Piper automatically when it's installed, rather than
    leaving the line silent over an upstream block outside anyone's
    control. Re-raises the original error if Piper isn't available, so
    the line is still recorded as failed the normal way."""
    try:
        synthesize_line(text, voice, out_path, rate=rate)
    except EdgeTTSBlockedError as blocked:
        try:
            import piper  # noqa: F401 -- just checking it's installed
        except ImportError:
            raise blocked
        offline_voice = character_voice_map.get(speaker, DEFAULT_OFFLINE_VOICE_POOL[0])
        synthesize_line_offline(text, offline_voice, out_path)


# ---------------------------------------------------------------------------
# Voice cloning (ElevenLabs) -- hosted API alternative to F5-TTS. No GPU,
# no local model download; you upload a reference clip once per
# character to create a cloned voice, then reuse its voice_id. Paid
# service (has a free tier with limits) but much more likely to "just
# work" on the first try than a local model.
# ---------------------------------------------------------------------------

def clone_voice_elevenlabs(api_key: str, character_name: str, ref_audio_path: str) -> str:
    """One-time setup per character: uploads a reference clip and
    returns a voice_id to reuse for all of that character's lines.
    Requires `pip install elevenlabs`."""
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=api_key)
    voice = client.voices.ivc.create(name=character_name, files=[open(ref_audio_path, "rb")])
    return voice.voice_id


def synthesize_line_elevenlabs(api_key: str, text: str, voice_id: str, out_path: str,
                                model: str = "eleven_multilingual_v2"):
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=api_key)
    audio = client.text_to_speech.convert(voice_id=voice_id, text=text, model_id=model)
    with open(out_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return out_path


# ---------------------------------------------------------------------------
# Voice cloning (F5-TTS) -- optional, needs local model + GPU recommended
# ---------------------------------------------------------------------------

_f5tts_model = None


def _get_f5tts_model():
    """Lazily loads F5-TTS. Requires `pip install f5-tts` and, on first
    run, downloads model checkpoints (needs internet on your machine).
    NOTE: written against F5-TTS's documented Python API but not run
    end-to-end in this environment -- sanity-check on one short line
    before batch-processing a whole drama."""
    global _f5tts_model
    if _f5tts_model is None:
        from f5_tts.api import F5TTS
        _f5tts_model = F5TTS()
    return _f5tts_model


def synthesize_line_cloned(text: str, ref_audio_path: str, ref_text: str, out_path: str):
    """Zero-shot voice cloning: generates `text` in the voice from
    `ref_audio_path` (a clean few-second clip of the target voice),
    using `ref_text` (an accurate transcript of what's said in that
    clip -- required by F5-TTS to anchor the voice characteristics)."""
    model = _get_f5tts_model()
    model.infer(ref_file=ref_audio_path, ref_text=ref_text, gen_text=text, file_wave=out_path)
    return out_path


def extract_reference_clips(audio_path: str, lines, speaker_segments, drama_dir: str,
                             min_duration: float = 3.0, max_duration: float = 12.0):
    """For each detected speaker, finds one reasonably clean, isolated
    segment of their voice (not overlapping another speaker) to use as
    a cloning reference clip. Returns {speaker_label: clip_path}.
    Pair this with the matching line's Chinese text as `ref_text` when
    calling synthesize_line_cloned -- the original audio's own words,
    not the translation, since the clip is still in the original voice."""
    from pydub import AudioSegment
    audio = AudioSegment.from_file(audio_path)

    best_by_speaker = {}
    for seg in speaker_segments:
        dur = seg["end"] - seg["start"]
        if not (min_duration <= dur <= max_duration):
            continue
        prev_best = best_by_speaker.get(seg["speaker"])
        # prefer clips closest to ~6s -- long enough to anchor voice, short enough to stay clean
        score = -abs(dur - 6.0)
        if prev_best is None or score > prev_best[0]:
            best_by_speaker[seg["speaker"]] = (score, seg)

    ref_clips_dir = os.path.join(drama_dir, "voice_refs")
    os.makedirs(ref_clips_dir, exist_ok=True)
    out = {}
    for speaker, (_, seg) in best_by_speaker.items():
        clip = audio[int(seg["start"] * 1000):int(seg["end"] * 1000)]
        clip_path = os.path.join(ref_clips_dir, f"{speaker}.wav")
        clip.export(clip_path, format="wav")
        out[speaker] = {"path": clip_path, "start": seg["start"], "end": seg["end"]}
    return out


def assign_voices_to_characters(speaker_labels, voice_pool=None):
    """Round-robin assignment of TTS voices to speaker labels, as a
    starting point -- override per-character in the UI/DB afterwards."""
    voice_pool = voice_pool or DEFAULT_VOICE_POOL
    return {label: voice_pool[i % len(voice_pool)] for i, label in enumerate(sorted(speaker_labels))}


def _speed_rate_for_line(text: str, duration: float) -> str:
    """Rough heuristic: estimate needed speaking rate so the dubbed line
    fits the original line's time slot, so it stays roughly in sync."""
    if duration <= 0:
        return "+0%"
    est_seconds = max(len(text.split()) / 2.5, 0.5)  # ~150 wpm baseline
    ratio = est_seconds / duration
    if ratio <= 1.05:
        return "+0%"
    pct = min(int((ratio - 1) * 100), 60)  # cap speedup at +60%
    return f"+{pct}%"


def build_dub_track(lines, drama_dir: str, character_voice_map: dict,
                     default_voice: str = "en-US-AvaNeural", progress_cb=None,
                     character_clone_map: dict = None, tts_engine: str = "edge_tts"):
    """
    Synthesizes one clip per line, placed at its correct timestamp, and
    mixes them into a single dub track for the whole episode.
    Returns (path_to_mixed_wav, errors) -- errors is a list of
    {"line_idx", "error"} for any line whose synthesis failed after
    retries. Failed lines are simply left silent in the mix rather than
    aborting the whole track -- so one bad line doesn't cost you every
    other line's already-generated audio.
    Requires ffmpeg on PATH (same requirement as the alignment step).

    character_clone_map: optional {speaker_label: {"ref_audio": path,
    "ref_text": str}}. If present for a given line's speaker, uses
    voice cloning (F5-TTS) instead of the fallback TTS.

    tts_engine: "edge_tts" (free online, more natural) or "offline"
    (Piper, fully local/no internet). Cloning (if a clone map entry
    exists for the speaker) always takes priority over either.
    """
    from pydub import AudioSegment
    from translate_engines import call_with_backoff

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    character_clone_map = character_clone_map or {}
    errors = []

    total_end = max((ln.end for ln in lines), default=0.0)
    track = AudioSegment.silent(duration=int(total_end * 1000) + 2000)

    n = len(lines)
    for i, ln in enumerate(lines):
        if not ln.en.strip():
            continue
        clip_path = os.path.join(clips_dir, f"line_{ln.idx:04d}.wav")
        # already-generated clips (e.g. from a prior partial run) are reused, not re-synthesized
        if os.path.exists(clip_path):
            try:
                clip = AudioSegment.from_file(clip_path)
                track = track.overlay(clip, position=int(ln.start * 1000))
                ln.dub_filename = os.path.relpath(clip_path, drama_dir)
                if progress_cb:
                    progress_cb((i + 1) / n)
                continue
            except Exception:
                pass  # corrupt leftover clip -- fall through and regenerate it

        clone = character_clone_map.get(ln.speaker)
        try:
            if clone and clone.get("engine") == "elevenlabs":
                call_with_backoff(lambda: synthesize_line_elevenlabs(
                    clone["api_key"], ln.en, clone["voice_id"], clip_path))
            elif clone:
                call_with_backoff(lambda: synthesize_line_cloned(
                    ln.en, clone["ref_audio"], clone["ref_text"], clip_path))
            elif tts_engine == "offline":
                voice = character_voice_map.get(ln.speaker, DEFAULT_OFFLINE_VOICE_POOL[0])
                call_with_backoff(lambda: synthesize_line_offline(ln.en, voice, clip_path))
            else:
                voice = character_voice_map.get(ln.speaker, default_voice)
                rate = _speed_rate_for_line(ln.en, ln.end - ln.start)
                call_with_backoff(lambda: _synthesize_edge_tts_with_piper_fallback(
                    ln.en, voice, clip_path, character_voice_map, ln.speaker, rate=rate))
        except Exception as e:
            errors.append({"line_idx": ln.idx, "error": str(e)})
            if progress_cb:
                progress_cb((i + 1) / n)
            continue  # this line stays silent in the mix; everything else proceeds

        ln.dub_filename = os.path.relpath(clip_path, drama_dir)
        clip = AudioSegment.from_file(clip_path)
        track = track.overlay(clip, position=int(ln.start * 1000))
        if progress_cb:
            progress_cb((i + 1) / n)

    out_path = os.path.join(drama_dir, "dub_track.wav")
    track.export(out_path, format="wav")
    return out_path, errors


def build_narration_track(lines, drama_dir: str, character_voice_map: dict,
                           default_voice: str = "en-US-AvaNeural", progress_cb=None,
                           character_clone_map: dict = None, gap_ms: int = 350,
                           tts_engine: str = "edge_tts"):
    """
    For novel-narration mode: there's no pre-existing timing to sync
    to, so clips are generated and simply concatenated in order with a
    small gap between them. Mutates each line's .start/.end to the
    actual timing of its generated clip -- so you get a usable .srt
    alongside the narration audio.
    Returns (path_to_wav, errors) -- a failed line is skipped (silent
    gap inserted instead) rather than aborting the whole narration.
    """
    from pydub import AudioSegment
    from translate_engines import call_with_backoff

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    character_clone_map = character_clone_map or {}
    errors = []

    track = AudioSegment.silent(duration=0)
    cursor_ms = 0
    n = len(lines)
    for i, ln in enumerate(lines):
        if not ln.en.strip():
            ln.start = ln.end = cursor_ms / 1000.0
            continue
        clip_path = os.path.join(clips_dir, f"line_{ln.idx:04d}.wav")
        clone = character_clone_map.get(ln.speaker)

        if not os.path.exists(clip_path):
            try:
                if clone and clone.get("engine") == "elevenlabs":
                    call_with_backoff(lambda: synthesize_line_elevenlabs(
                        clone["api_key"], ln.en, clone["voice_id"], clip_path))
                elif clone:
                    call_with_backoff(lambda: synthesize_line_cloned(
                        ln.en, clone["ref_audio"], clone["ref_text"], clip_path))
                elif tts_engine == "offline":
                    voice = character_voice_map.get(ln.speaker, DEFAULT_OFFLINE_VOICE_POOL[0])
                    call_with_backoff(lambda: synthesize_line_offline(ln.en, voice, clip_path))
                else:
                    voice = character_voice_map.get(ln.speaker, default_voice)
                    call_with_backoff(lambda: _synthesize_edge_tts_with_piper_fallback(
                        ln.en, voice, clip_path, character_voice_map, ln.speaker))
            except Exception as e:
                errors.append({"line_idx": ln.idx, "error": str(e)})
                ln.start = cursor_ms / 1000.0
                cursor_ms += gap_ms
                ln.end = cursor_ms / 1000.0
                track += AudioSegment.silent(duration=gap_ms)
                if progress_cb:
                    progress_cb((i + 1) / n)
                continue

        ln.dub_filename = os.path.relpath(clip_path, drama_dir)
        clip = AudioSegment.from_file(clip_path)
        ln.start = cursor_ms / 1000.0
        track += clip
        cursor_ms += len(clip)
        ln.end = cursor_ms / 1000.0
        track += AudioSegment.silent(duration=gap_ms)
        cursor_ms += gap_ms
        if progress_cb:
            progress_cb((i + 1) / n)

    out_path = os.path.join(drama_dir, "narration_track.wav")
    track.export(out_path, format="wav")
    return out_path, errors


def build_track_subprocess_worker(lines, drama_dir, character_voice_map, default_voice,
                                  character_clone_map, tts_engine, is_narration, result_queue):
    """Step 4e: entry point for running build_dub_track()/build_narration_track()
    in its own OS process via background_jobs.start_process_job(), so
    Cancel can actually stop it. Confirmed safe to hard-stop: each line's
    clip is written to its own file one at a time, and both functions
    already reuse (rather than re-synthesize) any clip that exists from
    a prior partial run -- a kill mid-run loses at most the one clip
    that was mid-synthesis, which the next run regenerates on its own.

    Puts back the (mutated) lines -- both functions set .dub_filename
    per line, and build_narration_track also rewrites .start/.end to the
    clip's actual timing -- since the caller needs those values, not
    just out_path/errors. Must stay a plain, top-level, picklable
    function; lines are plain Line dataclasses, already picklable."""
    try:
        build_fn = build_narration_track if is_narration else build_dub_track
        out_path, errors = build_fn(
            lines, drama_dir, character_voice_map, default_voice=default_voice,
            character_clone_map=character_clone_map, tts_engine=tts_engine)
        result_queue.put(("ok", {"lines": lines, "out_path": out_path, "errors": errors}))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


def mux_dub_with_video_or_audio(original_media_path: str, dub_track_path: str, out_path: str,
                                 original_volume_db: float = -100.0):
    """
    Replaces (or nearly-silences) the original audio and lays the dub
    track on top. If original_media_path is a video, keeps the video
    stream; if it's audio-only, just outputs the mixed audio.
    Requires ffmpeg on PATH.
    """
    import subprocess
    is_video = os.path.splitext(original_media_path)[1].lower() in (".mp4", ".mkv", ".mov", ".webm")
    if is_video:
        cmd = [
            "ffmpeg", "-y", "-i", original_media_path, "-i", dub_track_path,
            "-filter_complex",
            f"[0:a]volume={original_volume_db}dB[orig];[orig][1:a]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", out_path,
        ]
    else:
        cmd = ["ffmpeg", "-y", "-i", dub_track_path, "-c:a", "libmp3lame", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
