"""
dub.py -- generates an AI dub/narration track from your translated lines.
Normally speaks the English translation; novel narration (build_narration_
track) can instead speak the drama's own source-language text (via
narrate_original) -- video/audio dubbing (build_dub_track) always speaks
the translation, since a video already has its own original-language audio.

Every voice comes from OmniVoice, the one voice engine (see CLONE_ENGINES).
Reference clips can be auto-extracted per speaker from the original audio
(extract_reference_clips()) or set manually. A speaker with no clip gets a
designed voice from a plain description. build_dub_track() and build_narration_track()
both take a character_clone_map (clone_map_from_characters() builds it, one
entry per speaker) and speak each line with that speaker's entry. Wired
into the UI at Workspace section 6 (extract/set reference clips, pick each
character's engine) and section 8 (dub generation itself).
"""

import os
import re
import json
import hashlib

# Plain descriptions OmniVoice can design a voice from, for speakers that
# have neither a clip nor a description of their own. No accent is named
# because the same pool is used for original-language (zh/ja/ko) narration.
DEFAULT_VOICE_DESCRIPTIONS = [
    "female, young adult, moderate pitch",
    "male, middle-aged, low pitch",
    "female, middle-aged, moderate pitch",
    "male, young adult, moderate pitch",
    "female, elderly, high pitch",
    "male, elderly, low pitch",
]

# ---------------------------------------------------------------------------
# The voice engine, chosen per character (characters.clone_engine) or, for
# speakers without one, per run. Optional and imported only when used.
# Written against the project's own documented Python API, not verified
# end-to-end -- sanity-check one short line before narrating a whole novel.
# ---------------------------------------------------------------------------

CLONE_ENGINES = {
    "omnivoice": "OmniVoice (clone from a clip, or describe a voice)",
}
DEFAULT_CLONE_ENGINE = "omnivoice"

# Engines that used to exist. Saved characters, voice bank entries and
# requests may still name them, so they are refused with a plain message
# instead of being treated as unknown (or silently swapped for another voice).
REMOVED_VOICE_ENGINES = {
    "edge_tts": "Edge TTS", "edge": "Edge TTS",
    "offline": "Piper", "piper": "Piper",
    "f5tts": "F5-TTS", "f5": "F5-TTS",
    "tada": "TADA",
    "chatterbox": "Chatterbox",
    "gpt_sovits": "GPT-SoVITS",
}


def removed_engine_message(engine):
    """The refusal text for an engine that was removed, else None."""
    label = REMOVED_VOICE_ENGINES.get(engine)
    return f"The {label} engine was removed. Pick another voice engine in Dub." if label else None


def engine_refusal(engine):
    """Why `engine` can't be used to generate (removed or unknown), else None."""
    if engine in CLONE_ENGINES:
        return None
    return removed_engine_message(engine) or "Unknown voice engine."


# Which engines are confirmed to speak non-English text well, for gating
# novel narration's "original language" mode. OmniVoice: k2-fsa's own
# release claims 600+ languages zero-shot, which covers zh/ja/ko.
CLONE_ENGINE_ORIGINAL_LANGUAGES = {
    "omnivoice": {"zh", "ja", "ko"},
}


def clone_engine_supports_language(engine: str, language: str) -> bool:
    """Whether `engine` is confirmed to generate `language` well. An engine
    not in CLONE_ENGINE_ORIGINAL_LANGUAGES (a removed one) isn't gated here
    -- always True; engine_refusal is what stops it."""
    if engine not in CLONE_ENGINE_ORIGINAL_LANGUAGES:
        return True
    return language in CLONE_ENGINE_ORIGINAL_LANGUAGES[engine]


# Engines that load a local model -- dub generation takes the GPU slot for
# these. Clips are generated one at a time: one shared model on one GPU
# gains nothing from threads.
LOCAL_MODEL_ENGINES = {"omnivoice"}


def _cuda_available() -> bool:
    import torch
    return torch.cuda.is_available()


def _check_vram(model_name):
    """Refuse a GPU load that clearly won't fit, with a
    plain message, before the model starts loading."""
    from services import vram_service
    vram_service.check_fits(model_name)


_omnivoice_model = None
OMNIVOICE_SAMPLE_RATE = 24000


def _get_omnivoice_model():
    """Lazily loads OmniVoice (`pip install omnivoice`, Apache-2.0). The
    first use downloads the k2-fsa/OmniVoice checkpoint."""
    global _omnivoice_model
    if _omnivoice_model is None:
        cuda = _cuda_available()
        if cuda:
            _check_vram("OmniVoice")
        import torch
        from omnivoice import OmniVoice
        _omnivoice_model = OmniVoice.from_pretrained(
            "k2-fsa/OmniVoice", device_map="cuda:0" if cuda else "cpu",
            dtype=torch.float16 if cuda else torch.float32)
    return _omnivoice_model


def synthesize_line_omnivoice(text: str, out_path: str, ref_audio_path: str = None,
                              ref_text: str = None, instruct: str = None):
    """Clones the voice in ref_audio_path (3-10s works best), or -- with no
    clip at all -- designs one from a plain description (instruct, e.g.
    "female, low pitch, british accent"). ref_text is optional: without
    it OmniVoice transcribes the clip itself with Whisper."""
    import soundfile as sf
    kwargs = {}
    if ref_audio_path:
        kwargs["ref_audio"] = ref_audio_path
        if ref_text:
            kwargs["ref_text"] = ref_text
    elif instruct:
        kwargs["instruct"] = instruct
    audio = _get_omnivoice_model().generate(text=text, **kwargs)
    sf.write(out_path, audio[0], OMNIVOICE_SAMPLE_RATE)
    return out_path


def _synthesize_cloned(clone: dict, text: str, out_path: str):
    """Routes one character_clone_map entry to its engine. A missing or
    broken engine install surfaces as a plain sentence, not an ImportError."""
    engine = clone.get("engine")
    refusal = engine_refusal(engine)
    if refusal:
        raise RuntimeError(refusal)
    try:
        return synthesize_line_omnivoice(
            text, out_path, ref_audio_path=clone.get("ref_audio"),
            ref_text=clone.get("ref_text"), instruct=clone.get("instruct"))
    except ImportError as e:
        # A package that is installed but can't load (e.g. a transformers
        # version clash) must not reach the user as a raw traceback.
        raise RuntimeError(f"The {CLONE_ENGINES[engine].split(' (')[0]} engine could not start. "
                           "Check it in Diagnostics.") from e


def clone_map_from_characters(characters, drama_dir: str,
                              default_engine: str = DEFAULT_CLONE_ENGINE,
                              speaker_labels=()) -> dict:
    """{speaker_label: clone entry} for build_dub_track/build_narration_track,
    from db.list_characters() rows -- shared by the Workspace tab and
    cli.py's dub command so both route every speaker the same way.
    Per character, first match wins (the engine is the character's
    clone_engine, else default_engine, the run's choice):
      1. a reference clip, cloned with that engine;
      2. a voice description (OmniVoice voice design, no clip needed).
    A character whose engine was removed or is unknown gets no entry (see
    engine_blockers, which the caller checks before generating).
    speaker_labels: every speaker in the lines (None for lines with no
    speaker). One with no entry yet gets a distinct designed voice when
    default_engine is OmniVoice. A character's elevenlabs_voice_id (hosted
    cloning, since removed) is ignored -- see clone_removed_message()."""
    out = {}
    for c in characters:
        label = c["speaker_label"]
        engine = c.get("clone_engine") or default_engine
        if engine not in CLONE_ENGINES:
            continue
        if c.get("ref_audio_filename"):
            out[label] = {"engine": engine, "ref_audio": os.path.join(drama_dir, c["ref_audio_filename"]),
                          "ref_text": c.get("ref_text") or ""}
        elif (c.get("voice_design") or "").strip():
            out[label] = {"engine": "omnivoice", "instruct": c["voice_design"].strip()}

    if default_engine == "omnivoice":
        bare = sorted((s for s in set(speaker_labels) if s not in out), key=lambda s: s or "")
        taken = {e["instruct"] for e in out.values() if e.get("instruct")}
        pool = [d for d in DEFAULT_VOICE_DESCRIPTIONS if d not in taken] or DEFAULT_VOICE_DESCRIPTIONS
        for i, label in enumerate(bare):
            out[label] = {"engine": "omnivoice", "instruct": pool[i % len(pool)]}
    return out


def engine_blockers(characters, tts_engine) -> list:
    """Plain messages for every stored engine that can no longer generate:
    the run's own engine and each character's clone_engine. Empty means
    nothing stands in the way. The rows themselves are never touched."""
    out = []
    refusal = engine_refusal(tts_engine)
    if refusal:
        out.append(refusal)
    for c in characters:
        engine = c.get("clone_engine")
        if engine and engine_refusal(engine):
            name = c.get("character_name") or c["speaker_label"]
            out.append(f"{name}: {engine_refusal(engine)}")
    return out


# Hosted cloning was removed, but characters cloned with it keep
# their stored voice id (the column stays, as the record of how their
# already-generated audio was made) -- they're simply not cloned any more.
REMOVED_CLONE_MESSAGE = ("This character was previously cloned via ElevenLabs, which has been "
                         "removed. Re-clone via OmniVoice to keep using a cloned "
                         "voice for them.")


def clone_removed_message(character):
    """REMOVED_CLONE_MESSAGE for a db.list_characters() row that was
    cloned with the removed hosted engine, else None."""
    return REMOVED_CLONE_MESSAGE if character.get("elevenlabs_voice_id") else None


def clone_map_uses_local_model(character_clone_map: dict) -> bool:
    """Whether generating with this map needs the GPU slot."""
    return any(v.get("engine") in LOCAL_MODEL_ENGINES
               for v in (character_clone_map or {}).values())


def extract_reference_clips(audio_path: str, speaker_segments, drama_dir: str,
                             min_duration: float = 3.0, max_duration: float = 12.0):
    """For each detected speaker, finds one reasonably clean, isolated
    segment of their voice (not overlapping another speaker) to use as
    a cloning reference clip. Returns (clips, skipped):

    clips: {speaker_label: {"path", "start", "end"}} for every speaker
    a suitable segment was found for. Pair this with the matching
    line's Chinese text as `ref_text` when calling synthesize_line_omnivoice
    -- the original audio's own words, not the translation, since the
    clip is still in the original voice.

    skipped: {speaker_label: {"closest_duration", "reason"}} for every
    OTHER speaker who has segments but none in [min_duration,
    max_duration] -- reason is "too_short" or "too_long", naming which
    bound their closest available segment actually missed, so a caller
    can explain the gap instead of a bare "no clone reference set" that
    looks identical to auto-extract never having run at all."""
    from pydub import AudioSegment
    audio = AudioSegment.from_file(audio_path)

    best_by_speaker = {}
    durations_by_speaker = {}
    for seg in speaker_segments:
        dur = seg["end"] - seg["start"]
        durations_by_speaker.setdefault(seg["speaker"], []).append(dur)
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

    skipped = {}
    for speaker, durations in durations_by_speaker.items():
        if speaker in out:
            continue
        closest = min(durations, key=lambda d: (min_duration - d) if d < min_duration else (d - max_duration))
        skipped[speaker] = {"closest_duration": closest,
                            "reason": "too_short" if closest < min_duration else "too_long"}
    return out, skipped


# A clip's filename carries a short signature of exactly what
# it was synthesized from, not just the line's index -- otherwise a line
# edited after dubbing silently reused its old audio, since a file already
# existed at that path. Unchanged lines keep the same name, so a cancelled
# run still resumes from every clip it already finished.
def clip_signature(text: str, voice: dict) -> str:
    """Short hash of the text sent to TTS plus the voice/engine settings
    (a character_clone_map entry)."""
    blob = json.dumps([text, voice], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def _voice_for_signature(clone, lang=None) -> dict:
    """lang: the narration language mode ("zh"/"ja"/"ko" for
    original-language narration, omitted/None for ordinary translation-mode
    narration or dubbing) -- folded into the signature explicitly so
    switching a drama's narration language always regenerates its clips,
    rather than relying on the spoken text alone happening to differ."""
    out = dict(clone)
    if lang:
        out["narration_lang"] = lang
    return out


# A dubbed clip that doesn't match its line's original time
# window is time-stretched to fit -- pitch-preserving (ffmpeg's atempo),
# but never faster than DUB_MAX_SPEEDUP (past that it sounds chipmunked)
# or slower than DUB_MAX_SLOWDOWN. A line needing more speed-up than the
# cap gets the cap and is allowed to overflow its window rather than be
# crushed to fit. This is the fallback after the pacing rewrite
# (translate_engines.rewrite_for_pacing_llm), not a replacement for it --
# a rewritten, shorter line simply needs less stretch. Starting values are
# youtube-auto-dub's own defaults; both are adjustable per run.
DUB_MAX_SPEEDUP = 1.4
DUB_MAX_SLOWDOWN = 0.85
_STRETCH_TOLERANCE = 1e-3

PACING_FIT, PACING_STRETCHED, PACING_OVERFLOW = "fit", "stretched", "overflow"
PACING_ICONS = {PACING_FIT: "🟢", PACING_STRETCHED: "🟡", PACING_OVERFLOW: "🔴"}
PACING_FILENAME = "pacing.json"  # in dub_clips/, written by build_dub_track


def stretch_for_window(clip_ms: float, window_ms: float, max_speedup: float = DUB_MAX_SPEEDUP,
                       max_slowdown: float = DUB_MAX_SLOWDOWN):
    """(factor, status) for a clip clip_ms long placed in a window
    window_ms long. factor is the playback speed to apply (1.0 = leave
    the clip alone), rounded to 3 decimals. status:
      PACING_FIT       -- fits its window without being sped up (a clip
                          shorter than its window may still be slowed
                          toward it, down to max_slowdown);
      PACING_STRETCHED -- sped up, within max_speedup, to fit;
      PACING_OVERFLOW  -- needed more than max_speedup, so it gets the cap
                          and still runs over its window."""
    if window_ms <= 0:
        return 1.0, PACING_OVERFLOW if clip_ms > 0 else PACING_FIT
    needed = clip_ms / window_ms
    if needed < 1.0:
        factor, status = max(needed, max_slowdown), PACING_FIT
    elif needed <= max_speedup:
        factor, status = needed, PACING_STRETCHED
    else:
        factor, status = max_speedup, PACING_OVERFLOW
    if abs(factor - 1.0) <= _STRETCH_TOLERANCE:
        factor = 1.0
        if status == PACING_STRETCHED:
            status = PACING_FIT
    return round(factor, 3), status


# A dub clip is seconds long, so this only stops a hung ffmpeg.
CLIP_FFMPEG_TIMEOUT_SECONDS = 300.0
# Encoding a whole narration track; same ceiling the export jobs use.
M4B_ENCODE_TIMEOUT_SECONDS = 4 * 3600


def time_stretch(in_path: str, out_path: str, factor: float):
    """Changes a clip's speed by factor without changing its pitch, via
    ffmpeg's atempo filter (one filter covers 0.5-2.0, wider than any
    sensible clamp). Written under a temporary name first, so a Cancel
    mid-write never leaves a partial clip for the next run to reuse."""
    import subprocess
    partial = out_path[:-len(".wav")] + ".partial.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", in_path, "-filter:a", f"atempo={factor:.3f}", partial],
                       check=True, capture_output=True, timeout=CLIP_FFMPEG_TIMEOUT_SECONDS)
        os.replace(partial, out_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return out_path


def load_pacing(drama_dir: str) -> dict:
    """{line_idx: {"status", "factor", "clip_ms", "window_ms",
    "dub_filename"}} from the last build_dub_track run, or {} if there
    isn't one (or it can't be read)."""
    try:
        with open(os.path.join(drama_dir, "dub_clips", PACING_FILENAME), encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def pacing_for_line(ln, pacing: dict):
    """The line's pacing record, only while it still describes the clip
    the line currently points at -- None once the line was re-dubbed or
    its clip cleared since."""
    rec = pacing.get(ln.idx)
    if rec and ln.dub_filename and rec.get("dub_filename") == ln.dub_filename:
        return rec
    return None


NO_VOICE_ERROR = "This speaker has no voice set up. Upload or extract a clip, or describe a voice."


def build_dub_track(lines, drama_dir: str, character_clone_map: dict, progress_cb=None,
                     max_speedup: float = DUB_MAX_SPEEDUP, max_slowdown: float = DUB_MAX_SLOWDOWN):
    """
    Synthesizes one clip per line, placed at its correct timestamp, and
    mixes them into a single dub track for the whole episode.
    Returns (path_to_mixed_wav, errors) -- errors is a list of
    {"line_idx", "error"} for any line whose synthesis failed after
    retries. Failed lines are simply left silent in the mix rather than
    aborting the whole track -- so one bad line doesn't cost you every
    other line's already-generated audio.
    Requires ffmpeg on PATH (same requirement as the alignment step).

    character_clone_map: {speaker_label: clone entry}, as
    clone_map_from_characters() builds it with every speaker in `lines`.
    A speaker with no entry has no voice: its lines fail with
    NO_VOICE_ERROR and stay silent.

    max_speedup/max_slowdown: the time-stretch clamp (see
    stretch_for_window). Each line's resulting pacing -- fit / stretched
    / still overflowing, and the factor applied -- is written to
    dub_clips/pacing.json (load_pacing reads it back).
    """
    from pydub import AudioSegment
    from translate_engines import call_with_backoff

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    errors = []

    total_end = max((ln.end for ln in lines), default=0.0)
    track = AudioSegment.silent(duration=int(total_end * 1000) + 2000)

    pacing = {}
    n = len(lines)
    for i, ln in enumerate(lines):
        if not ln.en.strip():
            continue
        clone = character_clone_map.get(ln.speaker or None)
        if clone is None:
            errors.append({"line_idx": ln.idx, "error": NO_VOICE_ERROR})
            if progress_cb:
                progress_cb((i + 1) / n)
            continue
        signature = clip_signature(ln.en, _voice_for_signature(clone))
        # The signature is part of the name (see clip_signature): an edited
        # line gets a fresh clip, an unchanged one is reused on resume.
        clip_path = os.path.join(clips_dir, f"line_{ln.idx:04d}_{signature}.wav")

        clip = None
        # already-generated clips (e.g. from a prior partial run) are reused, not re-synthesized
        if os.path.exists(clip_path):
            try:
                clip = AudioSegment.from_file(clip_path)
            except Exception:
                pass  # corrupt leftover clip -- fall through and regenerate it
        if clip is None:
            # Written under a temporary name and renamed once complete, so a
            # kill mid-synthesis (Cancel, Ctrl-C) never leaves a corrupted-
            # but-loadable clip -- e.g. a zero-frame WAV whose header
            # wave/soundfile already wrote before being killed -- at
            # clip_path for the "already exists" check above to reuse.
            partial = clip_path[:-len(".wav")] + ".partial.wav"
            try:
                call_with_backoff(lambda: _synthesize_cloned(clone, ln.en, partial))
                os.replace(partial, clip_path)
                clip = AudioSegment.from_file(clip_path)
            except Exception as e:
                if os.path.exists(partial):
                    os.remove(partial)
                errors.append({"line_idx": ln.idx, "error": str(e)})
                if progress_cb:
                    progress_cb((i + 1) / n)
                continue  # this line stays silent in the mix; everything else proceeds

        clip_ms, window_ms = len(clip), int(round((ln.end - ln.start) * 1000))
        factor, status = stretch_for_window(clip_ms, window_ms, max_speedup, max_slowdown)
        placed_path = clip_path
        if factor != 1.0:
            stretched_path = clip_path[:-len(".wav")] + f"_x{factor:.3f}.wav"
            try:
                if not os.path.exists(stretched_path):
                    time_stretch(clip_path, stretched_path, factor)
                clip = AudioSegment.from_file(stretched_path)
                placed_path = stretched_path
            except Exception:
                # No ffmpeg, or it failed: the unstretched clip still beats a
                # silent line -- recorded as what it actually is.
                factor = 1.0
                status = PACING_FIT if clip_ms <= window_ms else PACING_OVERFLOW
        ln.dub_filename = os.path.relpath(placed_path, drama_dir)
        track = track.overlay(clip, position=int(ln.start * 1000))
        pacing[ln.idx] = {"status": status, "factor": factor, "clip_ms": clip_ms,
                          "window_ms": window_ms, "dub_filename": ln.dub_filename}
        if progress_cb:
            progress_cb((i + 1) / n)

    with open(os.path.join(clips_dir, PACING_FILENAME), "w", encoding="utf-8") as f:
        json.dump(pacing, f)
    out_path = os.path.join(drama_dir, "dub_track.wav")
    track.export(out_path, format="wav")
    return out_path, errors


# Novel narration generates a text unit of up to this many characters in
# one TTS call -- several consecutive lines of the same speaker joined, for
# better cross-sentence prosody than one call per subtitle-sized line.
NARRATION_TTS_MAX_CHARS = 500

NOVEL_SOURCE_FILENAME = "novel_narration_source.txt"

# A line that's a chapter heading of its own: 第十二章 / 第3回 / 第二卷 /
# 序章 / "Chapter 12: Title" / "Prologue". Kept short and without
# sentence-ending punctuation, so an ordinary sentence that merely starts
# the same way (第一回合他就输了。 / "Chapter and verse, ...") isn't one.
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第\s*[0-9０-９零〇一二三四五六七八九十百千两兩]+\s*[章回节節卷]|序章|序言|楔子|引子|"
    r"尾声|尾聲|后记|後記|番外|(chapter\s+\S+|prologue|epilogue)\s*(?:[:：.\-–—]|$))", re.IGNORECASE)
_CHAPTER_HEADING_MAX_CHARS = 60
_SENTENCE_END = tuple("。！？!?…」』”\"")


def is_chapter_heading(ln) -> bool:
    for text in (ln.zh or "", ln.en or ""):
        text = text.strip()
        if (text and len(text) <= _CHAPTER_HEADING_MAX_CHARS and not text.endswith(_SENTENCE_END)
                and _CHAPTER_HEADING_RE.match(text)):
            return True
    return False


def narration_paragraph_ends(lines, drama_dir: str):
    """Set of line idx that end a paragraph of the novel's saved source
    text (see core.novel_paragraph_ends), or None if there's no source
    text or the lines no longer match it."""
    from core import novel_paragraph_ends
    path = os.path.join(drama_dir, NOVEL_SOURCE_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return novel_paragraph_ends(lines, f.read())


def _narration_steps(lines, character_clone_map, paragraph_ends, narrate_original: bool = False):
    """Splits narration lines, in order, into ("blank", line) for a line
    with nothing to say and ("unit", unit) for one TTS call. A unit joins
    consecutive lines while they share a speaker (so one voice), up to
    NARRATION_TTS_MAX_CHARS -- never across a paragraph end or a chapter
    heading, which always stands alone so it can mark an audiobook chapter.

    narrate_original: speaks ln.zh (the drama's source text)
    instead of ln.en -- for novel narration's "original language" mode."""
    steps, current = [], None
    for ln in lines:
        text = (ln.zh if narrate_original else ln.en).strip()
        if not text:
            steps.append(("blank", ln))
            current = None
            continue
        clone = character_clone_map.get(ln.speaker or None)
        heading = is_chapter_heading(ln)
        if (current is not None and not heading and current["speaker"] == ln.speaker
                and len(current["text"]) + 1 + len(text) <= NARRATION_TTS_MAX_CHARS):
            current["lines"].append(ln)
            current["text"] += " " + text
        else:
            current = {"lines": [ln], "text": text, "speaker": ln.speaker, "clone": clone,
                       "error": None}
            steps.append(("unit", current))
        if heading or (paragraph_ends is not None and ln.idx in paragraph_ends):
            current = None
    return steps


def build_narration_track(lines, drama_dir: str, character_clone_map: dict, progress_cb=None,
                           gap_ms: int = 350,
                           narrate_original: bool = False, source_language: str = "zh"):
    """
    For novel-narration mode: there's no pre-existing timing to sync
    to, so clips are generated and simply concatenated in order with a
    small gap between them. Mutates each line's .start/.end to the
    actual timing of its generated audio -- so you get a usable .srt
    alongside the narration audio.

    narrate_original/source_language: when narrate_original,
    speaks ln.zh (the drama's source text, in source_language) instead of
    ln.en -- caller is responsible for warning that exported subtitles
    still need ln.en to stay bilingual.

    Each TTS call covers a unit of several consecutive lines where it
    can (see _narration_steps), so the audio has cross-sentence prosody
    while each line stays its own, shorter subtitle cue: a unit's clip
    time is divided back across its lines in proportion to their length
    (resegment.split_times, the same split re-segmentation uses), and
    every line in the unit points at the one shared clip.

    Clips are generated first, one at a time -- any that already exist from
    a prior run are reused, not regenerated -- then assembled in order.
    character_clone_map is as for build_dub_track; a speaker with no entry
    (NO_VOICE_ERROR) is treated like any other failed unit.

    Returns (path_to_wav, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    """
    from types import SimpleNamespace
    from pydub import AudioSegment
    from translate_engines import call_with_backoff
    from resegment import split_times

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    errors = []

    steps = _narration_steps(lines, character_clone_map,
                             narration_paragraph_ends(lines, drama_dir), narrate_original=narrate_original)
    units = [unit for kind, unit in steps if kind == "unit"]
    for unit in units:
        first, last = unit["lines"][0].idx, unit["lines"][-1].idx
        if unit["clone"] is None:
            unit["error"] = NO_VOICE_ERROR
            continue
        signature = clip_signature(unit["text"], _voice_for_signature(
            unit["clone"],
            lang=(source_language if narrate_original else None)))
        span = f"{first:04d}" if first == last else f"{first:04d}-{last:04d}"
        # Signature in the name for the same reason as build_dub_track's
        # clips: an edited unit gets a fresh clip, an unchanged one is reused.
        unit["clip_path"] = os.path.join(clips_dir, f"line_{span}_{signature}.wav")

    def generate(unit):
        """Returns an error string instead of raising, so one failed unit
        can't stop the rest."""
        # Written under a temporary name and renamed once complete, so a
        # Cancel (which kills the process, possibly mid-write) never leaves
        # a partial clip behind for the next run's "already exists" check
        # to reuse.
        partial = unit["clip_path"][:-len(".wav")] + ".partial.wav"
        try:
            call_with_backoff(lambda: _synthesize_cloned(unit["clone"], unit["text"], partial))
            os.replace(partial, unit["clip_path"])
            return None
        except Exception as e:
            if os.path.exists(partial):
                os.remove(partial)
            return str(e)

    finished = 0

    def record(unit, error):
        nonlocal finished
        unit["error"] = error
        finished += 1
        if progress_cb:
            progress_cb(finished / len(units))

    for unit in units:
        if unit["error"]:
            record(unit, unit["error"])
        elif os.path.exists(unit["clip_path"]):
            record(unit, None)
        else:
            record(unit, generate(unit))

    track = AudioSegment.silent(duration=0)
    cursor_ms = 0
    for kind, item in steps:
        if kind == "blank":
            item.start = item.end = cursor_ms / 1000.0
            continue
        if item["error"]:
            for ln in item["lines"]:
                errors.append({"line_idx": ln.idx, "error": item["error"]})
                ln.start = cursor_ms / 1000.0
                cursor_ms += gap_ms
                ln.end = cursor_ms / 1000.0
                track += AudioSegment.silent(duration=gap_ms)
            continue

        clip = AudioSegment.from_file(item["clip_path"])
        start_ms, end_ms = cursor_ms, cursor_ms + len(clip)
        members = item["lines"]
        # Split proportionally by whichever text was actually
        # spoken (narrate_original: the source text, not the translation).
        _split_texts = [(ln.zh if narrate_original else ln.en) for ln in members]
        cuts = (split_times(SimpleNamespace(start=start_ms, end=end_ms), _split_texts)
                if len(members) > 1 else [])
        edges = [start_ms] + cuts + [end_ms]
        dub_filename = os.path.relpath(item["clip_path"], drama_dir)
        for k, ln in enumerate(members):
            ln.dub_filename = dub_filename
            ln.start = edges[k] / 1000.0
            ln.end = edges[k + 1] / 1000.0
        track += clip
        cursor_ms = end_ms
        track += AudioSegment.silent(duration=gap_ms)
        cursor_ms += gap_ms

    out_path = os.path.join(drama_dir, "narration_track.wav")
    track.export(out_path, format="wav")
    return out_path, errors


def narration_chapters(lines, paragraph_ends=None, narrate_original: bool = False) -> list:
    """[(start_seconds, title), ...] for an audiobook's chapter markers,
    from lines already timed by build_narration_track. The novel's own
    chapter headings when it has any; otherwise one chapter per paragraph
    of its source text; otherwise (source text missing or edited since)
    one per generated clip, which never crosses a paragraph end.

    narrate_original: "voiced" and chapter titles come from
    ln.zh (what was actually spoken) instead of ln.en."""
    text_field = "zh" if narrate_original else "en"
    voiced = [ln for ln in lines if getattr(ln, text_field).strip()]
    if not voiced:
        return []
    headings = [ln for ln in voiced if is_chapter_heading(ln)]
    if headings:
        starts = headings if headings[0] is voiced[0] else [voiced[0]] + headings
    elif paragraph_ends is not None:
        starts, new_paragraph = [], True
        for ln in lines:
            if getattr(ln, text_field).strip() and new_paragraph:
                starts.append(ln)
                new_paragraph = False
            if ln.idx in paragraph_ends:
                new_paragraph = True
    else:
        starts, previous_clip = [], object()
        for ln in voiced:
            if ln.dub_filename != previous_clip:
                starts.append(ln)
            previous_clip = ln.dub_filename
    chapters = []
    for ln in starts:
        title = " ".join(getattr(ln, text_field).split())
        chapters.append((ln.start, title if len(title) <= 60 else title[:59] + "…"))
    return chapters


def _ffmetadata_escape(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _wav_duration_ms(path: str):
    import wave
    try:
        with wave.open(path, "rb") as w:
            return int(w.getnframes() * 1000 / w.getframerate())
    except Exception:
        return None


def narration_ffmetadata(chapters, total_ms: int, title: str = None) -> str:
    """ffmpeg's FFMETADATA1 text for the given chapters. A chapter that
    wouldn't start after the one before it (two markers on the same
    instant) is dropped rather than written as a zero-length chapter."""
    out = [";FFMETADATA1"]
    if title:
        out.append(f"title={_ffmetadata_escape(title)}")
    kept = []
    for start_s, name in chapters:
        start_ms = int(round(start_s * 1000))
        if start_ms < total_ms and (not kept or start_ms > kept[-1][0]):
            kept.append((start_ms, name))
    for i, (start_ms, name) in enumerate(kept):
        end_ms = kept[i + 1][0] if i + 1 < len(kept) else total_ms
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                f"title={_ffmetadata_escape(name)}"]
    return "\n".join(out) + "\n"


def export_narration_m4b(lines, drama_dir: str, title: str = None, out_path: str = None,
                         narrate_original: bool = False, cancel_job_id: str = None) -> str:
    """Encodes narration_track.wav as an M4B audiobook (AAC) with chapter
    markers (narration_chapters). Needs the narration generated first --
    lines carrying the timing build_narration_track gave them -- and
    ffmpeg on PATH. Returns the .m4b path. narrate_original:
    chapter titles come from the source text, matching the narration
    audio's own language. cancel_job_id: a thread job whose cancel request
    kills the ffmpeg run (background_jobs.run_cancellable)."""
    import subprocess
    wav_path = os.path.join(drama_dir, "narration_track.wav")
    if not os.path.exists(wav_path):
        raise FileNotFoundError("No narration_track.wav yet -- generate the narration first.")
    total_ms = _wav_duration_ms(wav_path) or int(max((ln.end for ln in lines), default=0.0) * 1000)
    chapters = narration_chapters(lines, narration_paragraph_ends(lines, drama_dir),
                                  narrate_original=narrate_original)
    meta_path = os.path.join(drama_dir, "narration_chapters.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(narration_ffmetadata(chapters, total_ms, title))
    out_path = out_path or os.path.join(drama_dir, "narration.m4b")
    cmd = ["ffmpeg", "-y", "-i", wav_path, "-i", meta_path, "-map", "0:a",
           "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "64k",
           "-f", "ipod", out_path]
    if cancel_job_id:
        import background_jobs
        background_jobs.run_cancellable(cancel_job_id, cmd)
    else:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=M4B_ENCODE_TIMEOUT_SECONDS)
    return out_path


BACKGROUND_FILENAME = "dub_background.wav"
# Sidecar next to the cached background: what it was separated from and how.
BACKGROUND_META_FILENAME = "dub_background.json"
BACKGROUND_GAIN_DB = -6.0


def _background_cache_key(source_audio_path: str, backend: str) -> dict:
    """What the cached dub_background.wav must have been made from to be
    reused: the separation backend asked for and the source audio's name,
    size and modification time."""
    st = os.stat(source_audio_path)
    return {"backend": backend, "source": os.path.basename(source_audio_path),
            "source_size": st.st_size, "source_mtime_ns": st.st_mtime_ns}


def _read_background_meta(meta_path: str):
    try:
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def mix_original_background(track_path: str, source_audio_path: str, drama_dir: str,
                            backend: str = "auto", gain_db: float = BACKGROUND_GAIN_DB,
                            progress_cb=None, cancel_check_cb=None) -> str:
    """Lays the original recording's background (music/ambience/
    effects, i.e. the source minus its vocals) back under a finished dub
    track, rewriting track_path in place. The separated background is cached
    in drama_dir and reused, since separation is the slow part, while its
    sidecar (BACKGROUND_META_FILENAME) still matches the backend and the
    source audio's name/size/mtime; otherwise it is separated again.
    Raises audio_preprocess.VocalSeparationError
    (backend missing/failed) or VocalSeparationCancelled."""
    import audio_preprocess
    from pydub import AudioSegment

    bg_path = os.path.join(drama_dir, BACKGROUND_FILENAME)
    meta_path = os.path.join(drama_dir, BACKGROUND_META_FILENAME)
    key = _background_cache_key(source_audio_path, backend)
    if not (os.path.exists(bg_path) and _read_background_meta(meta_path) == key):
        if os.path.exists(meta_path):
            os.remove(meta_path)   # never trust a half-rewritten background
        audio_preprocess.extract_background(
            source_audio_path, bg_path, backend=backend,
            progress_cb=progress_cb, cancel_check_cb=cancel_check_cb)
        tmp_meta = meta_path + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(key, f)
        os.replace(tmp_meta, meta_path)
    track = AudioSegment.from_file(track_path)
    background = AudioSegment.from_file(bg_path) + gain_db
    track.overlay(background).export(track_path, format="wav")
    return track_path


def build_track_subprocess_worker(lines, drama_dir, character_clone_map, is_narration,
                                  max_speedup, max_slowdown, result_queue,
                                  narrate_original=False, source_language="zh",
                                  background_source=None, separation_backend="auto"):
    """Entry point for running build_dub_track()/build_narration_track()
    in its own OS process via background_jobs.start_process_job(), so
    Cancel can actually stop it. Confirmed safe to hard-stop: each clip
    is written to its own file, and both functions already reuse (rather
    than re-synthesize) any clip that exists from a prior partial run -- a
    kill mid-run loses only the clip(s) mid-synthesis, which the next run
    regenerates on its own. (build_narration_track, which can have several
    clips in flight at once, writes each under a temporary name first.)

    Puts back the (mutated) lines -- both functions set .dub_filename
    per line, and build_narration_track also rewrites .start/.end to the
    clip's actual timing -- since the caller needs those values, not
    just out_path/errors. Must stay a plain, top-level, picklable
    function; lines are plain Line dataclasses, already picklable.
    max_speedup/max_slowdown: build_dub_track's time-stretch clamp (a
    narration has no timing to fit, so it ignores them).
    narrate_original/source_language: narration
    only (build_dub_track's video-dub path ignores both -- dubbing a video
    in its own original language doesn't make sense). background_source:
    path of the original audio; when given on a video dub, its
    background is mixed back under the finished track. A failed/missing
    separation never loses the dub -- the plain track is kept and the result
    carries background_mixed False plus a fixed background_error text."""
    try:
        if is_narration:
            out_path, errors = build_narration_track(
                lines, drama_dir, character_clone_map,
                narrate_original=narrate_original, source_language=source_language)
        else:
            out_path, errors = build_dub_track(
                lines, drama_dir, character_clone_map,
                max_speedup=max_speedup, max_slowdown=max_slowdown)
        result = {"lines": lines, "out_path": out_path, "errors": errors}
        if background_source and not is_narration:
            import audio_preprocess
            try:
                mix_original_background(out_path, background_source, drama_dir,
                                        backend=separation_backend)
                result["background_mixed"] = True
            except audio_preprocess.VocalSeparationError:
                result["background_mixed"] = False
                result["background_error"] = ("Background music could not be separated; "
                                              "the dub track was kept without it.")
        result_queue.put(("ok", result))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))
