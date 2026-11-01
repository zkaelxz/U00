"""
dub.py -- the voice layer shared by novel narration and voice cloning: the
one voice engine, per-speaker clone maps, reference-clip extraction and the
helpers that decide what a speaker's voice is. dub_narration.py builds the
narration track on top of it, speaking the translation or, via
narrate_original, the drama's own source-language text.

Every voice comes from OmniVoice, the one voice engine (see CLONE_ENGINES).
Reference clips can be auto-extracted per speaker from the original audio
(extract_reference_clips()) or set manually. A speaker with no clip gets a
designed voice from a plain description. build_narration_track() takes a
character_clone_map (clone_map_from_characters() builds it, one entry per
speaker) and speaks each line with that speaker's entry.
"""

import os
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
    return f"The {label} engine was removed. Pick another voice engine." if label else None


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
OMNIVOICE_REPO = "k2-fsa/OmniVoice"
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
            OMNIVOICE_REPO, device_map="cuda:0" if cuda else "cpu",
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
    from db.list_characters() rows -- used by the Workspace tab to route every speaker.
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


# Encoding a whole narration track; same ceiling the export jobs use.
M4B_ENCODE_TIMEOUT_SECONDS = 4 * 3600


NO_VOICE_ERROR = "This speaker has no voice set up. Upload or extract a clip, or describe a voice."
