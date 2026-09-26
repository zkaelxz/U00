"""
dub.py -- generates an English AI dub track from your translated lines.

Default engine: edge-tts (Microsoft, free, no cloning -- picks from a
fixed voice list). Assign a different TTS voice per character (via the
`characters` table) for a multi-voice cast.

VOICE CLONING (matching the original actors' actual voices) IS wired up,
via a per-character local engine -- F5-TTS, OmniVoice, GPT-SoVITS,
Chatterbox or TADA (see CLONE_ENGINES).
Reference clips can be auto-extracted per speaker from the original audio
(extract_reference_clips()) or set manually. A character with no clip can
still get its own voice from a plain description (OmniVoice voice design),
or Chatterbox's built-in voice. build_dub_track() and
build_narration_track() both take a character_clone_map
(clone_map_from_characters() builds it) and use whichever backend a
character has configured, falling back to the plain TTS engine only for
characters with none. Wired into the UI at Workspace section 6
(extract/set reference clips, pick each character's engine) and section 8
(dub generation itself).
"""

import os
import re
import json
import asyncio
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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


async def _edge_tts_synthesize(text: str, voice: str, out_path: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
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


def synthesize_line(text: str, voice: str, out_path: str):
    """Single hook point: swap this out for a voice-cloning backend later."""
    asyncio.run(_edge_tts_synthesize(text, voice, out_path))


# ---------------------------------------------------------------------------
# Offline TTS (Piper) -- fully local, no internet, no API cost
# ---------------------------------------------------------------------------

_piper_voices = {}
# Piper phonemizes through espeak-ng, whose C library keeps global state --
# build_narration_track's thread pool can reach Piper through edge-tts's
# automatic Piper fallback, so Piper calls never overlap.
_piper_lock = threading.Lock()

# A few good default Piper English voices (download once, reused after).
# Full catalog: https://github.com/rhasspy/piper/blob/master/VOICES.md
DEFAULT_OFFLINE_VOICE_POOL = [
    "en_US-amy-medium", "en_US-lessac-medium", "en_GB-alba-medium",
    "en_US-kristin-medium", "en_GB-jenny_dioco-medium",
]

# Piper's own voice-name shape, <lang>_<REGION>-<name>-<quality> -- the
# pattern piper-tts's own downloader matches against. An edge-tts name
# ("en-US-AvaNeural") never fits it.
_PIPER_VOICE_NAME = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[^-]+-[^-]+$")


def piper_voices_dir() -> str:
    """Where downloaded Piper voice models (.onnx + .onnx.json) live --
    inside the library folder, so a portable install carries them along."""
    import db
    return os.path.join(db.LIBRARY_DIR, "piper_voices")


def piper_model_path(voice: str) -> str:
    """Returns the local .onnx model path for Piper voice `voice`,
    downloading it (model + config, needs internet once) on first use.
    Covers both real piper-tts API generations: 1.3+ ships
    piper.download_voices.download_voice; 1.2 ships piper.download's
    get_voices/ensure_voice_exists instead."""
    if not _PIPER_VOICE_NAME.match(voice or ""):
        raise ValueError(f"'{voice}' isn't a Piper voice name (expected something like "
                         f"'{DEFAULT_OFFLINE_VOICE_POOL[0]}') -- pick an offline voice in section 6.")
    voices_dir = piper_voices_dir()
    os.makedirs(voices_dir, exist_ok=True)
    model_path = os.path.join(voices_dir, f"{voice}.onnx")
    if not (os.path.exists(model_path) and os.path.exists(model_path + ".json")):
        try:
            from piper.download_voices import download_voice
        except ImportError:
            from piper.download import ensure_voice_exists, get_voices
            ensure_voice_exists(voice, [voices_dir], voices_dir, get_voices(voices_dir))
        else:
            from pathlib import Path
            download_voice(voice, Path(voices_dir))
    return model_path


def synthesize_line_offline(text: str, voice: str, out_path: str):
    """Requires `pip install piper-tts`. `voice` is a Piper voice name
    (DEFAULT_OFFLINE_VOICE_POOL); its model is downloaded on first use
    (needs internet once) and fully offline after that. No per-line API
    cost."""
    import wave
    from piper import PiperVoice
    with _piper_lock:
        if voice not in _piper_voices:
            _piper_voices[voice] = PiperVoice.load(piper_model_path(voice))
        pv = _piper_voices[voice]
        with wave.open(out_path, "wb") as wav_file:
            if hasattr(pv, "synthesize_wav"):
                pv.synthesize_wav(text, wav_file)  # piper-tts 1.3+
            else:
                pv.synthesize(text, wav_file)  # piper-tts 1.2
    return out_path


def offline_voice_for(offline_voice_map: dict, speaker) -> str:
    """A speaker's Piper voice: its own offline voice from section 6, else
    the default. Only ever reads the offline map -- a character's edge-tts
    voice is a different engine's name that Piper can't load."""
    return (offline_voice_map or {}).get(speaker) or DEFAULT_OFFLINE_VOICE_POOL[0]


def _synthesize_edge_tts_with_piper_fallback(text: str, voice: str, out_path: str,
                                              offline_voice_map: dict, speaker):
    """Tries edge-tts; if Microsoft blocks the request (EdgeTTSBlockedError),
    falls back to Piper automatically when it's installed, rather than
    leaving the line silent over an upstream block outside anyone's
    control. Re-raises the original error if Piper isn't available, so
    the line is still recorded as failed the normal way."""
    try:
        synthesize_line(text, voice, out_path)
    except EdgeTTSBlockedError as blocked:
        try:
            import piper  # noqa: F401 -- just checking it's installed
        except ImportError:
            raise blocked
        synthesize_line_offline(text, offline_voice_for(offline_voice_map, speaker), out_path)


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


# ---------------------------------------------------------------------------
# Step 11b: more local voice engines, chosen per character
# (characters.clone_engine). Each is optional and imported only when a
# character actually uses it. Written against each project's own
# documented Python/HTTP API, not run end-to-end in this environment (no
# GPU, no model downloads) -- sanity-check one short line before
# narrating a whole novel.
# ---------------------------------------------------------------------------

# NULL clone_engine in the database means F5-TTS -- the only local cloning
# engine before Step 11b -- so characters set up earlier keep their voice.
CLONE_ENGINES = {
    "f5tts": "F5-TTS (clone from a clip)",
    "omnivoice": "OmniVoice (clone from a clip, or describe a voice)",
    "gpt_sovits": "GPT-SoVITS (clone from a clip; needs its own local server running)",
    "chatterbox": "Chatterbox (emotion-aware delivery; clip optional)",
    "tada": "TADA (stays on-script over long runs; clone from a clip)",
}
DEFAULT_CLONE_ENGINE = "f5tts"

# Engines that load a local model (or, for GPT-SoVITS, talk to a local
# model server) -- dub generation takes the GPU slot for these.
LOCAL_MODEL_ENGINES = {"f5tts", "omnivoice", "gpt_sovits", "chatterbox", "tada"}

# Engines whose calls may overlap in build_narration_track's thread pool:
# the network service (edge-tts), which is where the waiting is.
# Everything local stays single-threaded: one shared model on one GPU
# gains nothing from threads, and Chatterbox is confirmed unsafe -- its
# generate() stores the reference voice and exaggeration on the model
# itself (self.conds), so two overlapping calls could swap voices.
# GPT-SoVITS's server handles one request at a time (VideoLingo forces it
# single-threaded for the same reason); Piper is serialized by _piper_lock.
PARALLEL_SAFE_ENGINES = {"edge_tts"}


def _cuda_available() -> bool:
    import torch
    return torch.cuda.is_available()


_omnivoice_model = None
OMNIVOICE_SAMPLE_RATE = 24000


def _get_omnivoice_model():
    """Lazily loads OmniVoice (`pip install omnivoice`, Apache-2.0). The
    first use downloads the k2-fsa/OmniVoice checkpoint."""
    global _omnivoice_model
    if _omnivoice_model is None:
        import torch
        from omnivoice import OmniVoice
        cuda = _cuda_available()
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


GPT_SOVITS_DEFAULT_URL = "http://127.0.0.1:9880"
# GPT-SoVITS's own language codes for the reference clip's transcript.
_GPT_SOVITS_LANGUAGES = {"zh": "zh", "ja": "ja", "ko": "ko", "en": "en"}


def synthesize_line_gpt_sovits(text: str, ref_audio_path: str, ref_text: str, out_path: str,
                               ref_language: str = "zh", base_url: str = GPT_SOVITS_DEFAULT_URL):
    """GPT-SoVITS (MIT) isn't a pip package -- it runs as its own local
    server (`python api_v2.py` from its folder, port 9880 by default), the
    same way pyvideotrans and VideoLingo use it. ref_audio_path must be a
    3-10s clip; the server reads it from disk, so this passes an absolute
    path on the same machine."""
    import requests
    try:
        resp = requests.post(f"{base_url.rstrip('/')}/tts", json={
            "text": text, "text_lang": "en",
            "ref_audio_path": os.path.abspath(ref_audio_path),
            "prompt_text": ref_text or "",
            "prompt_lang": _GPT_SOVITS_LANGUAGES.get(ref_language, "zh"),
            "media_type": "wav",
        }, timeout=300)
    except requests.ConnectionError as e:
        raise RuntimeError(f"GPT-SoVITS server isn't reachable at {base_url} -- start it with "
                           "`python api_v2.py` in your GPT-SoVITS folder") from e
    if resp.status_code != 200:
        raise RuntimeError(f"GPT-SoVITS server error {resp.status_code}: {resp.text[:300]}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


_chatterbox = None  # (model, its built-in voice conditionals)


def _get_chatterbox():
    """Lazily loads Chatterbox (`pip install chatterbox-tts`, MIT). Every
    clip it generates carries Resemble AI's imperceptible PerTh
    watermark -- built into the model, not something this app adds."""
    global _chatterbox
    if _chatterbox is None:
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_pretrained(device="cuda" if _cuda_available() else "cpu")
        _chatterbox = (model, model.conds)
    return _chatterbox


def synthesize_line_chatterbox(text: str, out_path: str, ref_audio_path: str = None,
                               exaggeration: float = 0.5):
    """exaggeration: emotional intensity, 0.4-0.7 recommended -- see
    emotion.chatterbox_exaggeration(). With no ref_audio_path, speaks in
    Chatterbox's built-in voice."""
    import soundfile as sf
    model, builtin_voice = _get_chatterbox()
    if not ref_audio_path:
        # generate() leaves the last clip's voice on the model (self.conds);
        # without this, a character with no clip would speak in whichever
        # character's clip was used before it.
        model.conds = builtin_voice
    wav = model.generate(text, audio_prompt_path=ref_audio_path or None, exaggeration=exaggeration)
    sf.write(out_path, wav.squeeze(0).cpu().numpy(), model.sr)
    return out_path


TADA_MODEL_ID = "HumeAI/tada-3b-ml"
TADA_SAMPLE_RATE = 24000
# TADA's per-language aligners for the reference clip; anything else uses
# its default (English) aligner, guided by the clip's transcript.
_TADA_ALIGNER_LANGUAGES = {"zh": "ch", "ja": "ja"}
_tada = {"model": None, "encoders": {}, "prompts": {}}


def _get_tada(aligner_language):
    """Lazily loads TADA (`pip install hume-tada`). Code is MIT; the model
    weights are under Meta's Llama 3.2 Community License, and downloading
    them needs a Hugging Face account that has accepted that license."""
    import torch
    from tada.modules.encoder import Encoder
    from tada.modules.tada import TadaForCausalLM
    device = "cuda" if _cuda_available() else "cpu"
    if _tada["model"] is None:
        _tada["model"] = TadaForCausalLM.from_pretrained(
            TADA_MODEL_ID, torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32).to(device)
    if aligner_language not in _tada["encoders"]:
        kwargs = {"language": aligner_language} if aligner_language else {}
        _tada["encoders"][aligner_language] = Encoder.from_pretrained(
            "HumeAI/tada-codec", subfolder="encoder", **kwargs).to(device)
    return _tada["model"], _tada["encoders"][aligner_language], device


def synthesize_line_tada(text: str, ref_audio_path: str, ref_text: str, out_path: str,
                         ref_language: str = None):
    """TADA needs a reference clip (its "prompt") plus that clip's
    transcript. The encoded prompt is cached per clip, so a long narration
    encodes each character's clip once, not once per line."""
    import soundfile as sf
    import torch
    aligner = _TADA_ALIGNER_LANGUAGES.get(ref_language)
    model, encoder, device = _get_tada(aligner)
    key = (ref_audio_path, ref_text or "", aligner)
    prompt = _tada["prompts"].get(key)
    if prompt is None:
        data, sr = sf.read(ref_audio_path, dtype="float32", always_2d=True)
        audio = torch.from_numpy(data.mean(axis=1)).unsqueeze(0).to(device)
        kwargs = {"text": [ref_text]} if ref_text else {}
        prompt = encoder(audio, sample_rate=sr, **kwargs)
        _tada["prompts"][key] = prompt
    wav = model.generate(prompt=prompt, text=text).audio[0]
    if wav is None:
        raise RuntimeError("TADA produced no audio for this line")
    sf.write(out_path, wav.float().cpu().numpy(), TADA_SAMPLE_RATE)
    return out_path


def _synthesize_cloned(clone: dict, text: str, out_path: str, exaggeration: float = 0.5):
    """Routes one character_clone_map entry to its engine. An entry with
    no "engine" key is F5-TTS (the shape that predates Step 11b)."""
    engine = clone.get("engine", DEFAULT_CLONE_ENGINE)
    if engine == "omnivoice":
        return synthesize_line_omnivoice(text, out_path, ref_audio_path=clone.get("ref_audio"),
                                         ref_text=clone.get("ref_text"), instruct=clone.get("instruct"))
    if engine == "gpt_sovits":
        return synthesize_line_gpt_sovits(text, clone["ref_audio"], clone.get("ref_text"), out_path,
                                          ref_language=clone.get("ref_language", "zh"),
                                          base_url=clone.get("base_url") or GPT_SOVITS_DEFAULT_URL)
    if engine == "chatterbox":
        return synthesize_line_chatterbox(text, out_path, ref_audio_path=clone.get("ref_audio"),
                                          exaggeration=exaggeration)
    if engine == "tada":
        return synthesize_line_tada(text, clone["ref_audio"], clone.get("ref_text"), out_path,
                                    ref_language=clone.get("ref_language"))
    return synthesize_line_cloned(text, clone["ref_audio"], clone["ref_text"], out_path)


def clone_map_from_characters(characters, drama_dir: str,
                              gpt_sovits_url: str = None, ref_language: str = "zh") -> dict:
    """{speaker_label: clone entry} for build_dub_track/build_narration_track,
    from db.list_characters() rows -- shared by the Workspace tab and
    cli.py's dub command so both route every character the same way.
    Per character, first match wins:
      1. a reference clip, cloned with the character's clone_engine;
      2. a voice description (OmniVoice voice design, no clip needed);
      3. Chatterbox picked with no clip (its built-in voice, still
         emotion-aware).
    A character matching none isn't in the map, so it gets the plain TTS
    voice. A character's elevenlabs_voice_id (hosted cloning, removed in
    Step 11d) is ignored -- see clone_removed_message(). ref_language:
    the drama's source language -- the language spoken in its reference
    clips."""
    out = {}
    for c in characters:
        label = c["speaker_label"]
        engine = c.get("clone_engine") or DEFAULT_CLONE_ENGINE
        if c.get("ref_audio_filename"):
            entry = {"engine": engine, "ref_audio": os.path.join(drama_dir, c["ref_audio_filename"]),
                     "ref_text": c.get("ref_text") or ""}
            if engine in ("gpt_sovits", "tada"):
                entry["ref_language"] = ref_language
            if engine == "gpt_sovits":
                entry["base_url"] = gpt_sovits_url or GPT_SOVITS_DEFAULT_URL
            out[label] = entry
        elif (c.get("voice_design") or "").strip():
            out[label] = {"engine": "omnivoice", "instruct": c["voice_design"].strip()}
        elif engine == "chatterbox":
            out[label] = {"engine": "chatterbox", "ref_audio": None}
    return out


# Step 11d: hosted cloning was removed, but characters cloned with it keep
# their stored voice id (the column stays, as the record of how their
# already-generated audio was made) -- they're simply not cloned any more.
REMOVED_CLONE_MESSAGE = ("This character was previously cloned via ElevenLabs, which has been "
                         "removed. Re-clone via OmniVoice or GPT-SoVITS to keep using a cloned "
                         "voice for them.")


def clone_removed_message(character):
    """REMOVED_CLONE_MESSAGE for a db.list_characters() row that was
    cloned with the removed hosted engine, else None."""
    return REMOVED_CLONE_MESSAGE if character.get("elevenlabs_voice_id") else None


def clone_map_uses_local_model(character_clone_map: dict) -> bool:
    """Whether generating with this map needs the GPU slot."""
    return any(v.get("engine", DEFAULT_CLONE_ENGINE) in LOCAL_MODEL_ENGINES
               for v in (character_clone_map or {}).values())


def extract_reference_clips(audio_path: str, lines, speaker_segments, drama_dir: str,
                             min_duration: float = 3.0, max_duration: float = 12.0):
    """For each detected speaker, finds one reasonably clean, isolated
    segment of their voice (not overlapping another speaker) to use as
    a cloning reference clip. Returns (clips, skipped):

    clips: {speaker_label: {"path", "start", "end"}} for every speaker
    a suitable segment was found for. Pair this with the matching
    line's Chinese text as `ref_text` when calling synthesize_line_cloned
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


def assign_voices_to_characters(speaker_labels, voice_pool=None):
    """Round-robin assignment of TTS voices to speaker labels, as a
    starting point -- override per-character in the UI/DB afterwards."""
    voice_pool = voice_pool or DEFAULT_VOICE_POOL
    return {label: voice_pool[i % len(voice_pool)] for i, label in enumerate(sorted(speaker_labels))}


# Step 11e: a clip's filename carries a short signature of exactly what
# it was synthesized from, not just the line's index -- otherwise a line
# edited after dubbing silently reused its old audio, since a file already
# existed at that path. Unchanged lines keep the same name, so a cancelled
# run still resumes from every clip it already finished (Step 4e).
def clip_signature(text: str, voice: dict) -> str:
    """Short hash of the text sent to TTS plus the voice/engine settings
    (a character_clone_map entry, or the plain TTS engine and voice)."""
    voice = {k: v for k, v in voice.items() if k != "base_url"}  # where a server runs isn't the voice
    blob = json.dumps([text, voice], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def _voice_for_signature(clone, tts_engine, voice, exaggeration=None) -> dict:
    if clone:
        out = dict(clone)
        if clone.get("engine", DEFAULT_CLONE_ENGINE) == "chatterbox":
            out["exaggeration"] = exaggeration
        return out
    return {"engine": tts_engine, "voice": voice}


# Step 11c: a dubbed clip that doesn't match its line's original time
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


def time_stretch(in_path: str, out_path: str, factor: float):
    """Changes a clip's speed by factor without changing its pitch, via
    ffmpeg's atempo filter (one filter covers 0.5-2.0, wider than any
    sensible clamp). Written under a temporary name first, so a Cancel
    mid-write never leaves a partial clip for the next run to reuse."""
    import subprocess
    partial = out_path[:-len(".wav")] + ".partial.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", in_path, "-filter:a", f"atempo={factor:.3f}", partial],
                       check=True, capture_output=True)
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


def build_dub_track(lines, drama_dir: str, character_voice_map: dict,
                     default_voice: str = "en-US-AvaNeural", progress_cb=None,
                     character_clone_map: dict = None, tts_engine: str = "edge_tts",
                     emotion_map: dict = None, max_speedup: float = DUB_MAX_SPEEDUP,
                     max_slowdown: float = DUB_MAX_SLOWDOWN, offline_voice_map: dict = None):
    """
    Synthesizes one clip per line, placed at its correct timestamp, and
    mixes them into a single dub track for the whole episode.
    Returns (path_to_mixed_wav, errors) -- errors is a list of
    {"line_idx", "error"} for any line whose synthesis failed after
    retries. Failed lines are simply left silent in the mix rather than
    aborting the whole track -- so one bad line doesn't cost you every
    other line's already-generated audio.
    Requires ffmpeg on PATH (same requirement as the alignment step).

    character_clone_map: optional {speaker_label: clone entry}, as
    clone_map_from_characters() builds it. If present for a given line's
    speaker, uses that character's cloning/voice engine instead of the
    fallback TTS.

    tts_engine: "edge_tts" (free online, more natural) or "offline"
    (Piper, fully local/no internet). Cloning (if a clone map entry
    exists for the speaker) always takes priority over either.

    character_voice_map holds edge-tts voice names; offline_voice_map
    ({speaker_label: Piper voice name}) is what the offline engine -- and
    edge-tts's automatic Piper fallback -- uses instead.

    emotion_map: optional {line_idx: {"emotion", "intensity"}} (as
    db.load_emotions returns) -- sets Chatterbox's delivery per line.

    max_speedup/max_slowdown: the time-stretch clamp (see
    stretch_for_window). Each line's resulting pacing -- fit / stretched
    / still overflowing, and the factor applied -- is written to
    dub_clips/pacing.json (load_pacing reads it back).
    """
    from pydub import AudioSegment
    from translate_engines import call_with_backoff
    from emotion import chatterbox_exaggeration

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    character_clone_map = character_clone_map or {}
    emotion_map = emotion_map or {}
    errors = []

    total_end = max((ln.end for ln in lines), default=0.0)
    track = AudioSegment.silent(duration=int(total_end * 1000) + 2000)

    pacing = {}
    n = len(lines)
    for i, ln in enumerate(lines):
        if not ln.en.strip():
            continue
        clone = character_clone_map.get(ln.speaker)
        exaggeration = chatterbox_exaggeration(emotion_map.get(ln.idx)) if clone else None
        if clone:
            voice = None
        elif tts_engine == "offline":
            voice = offline_voice_for(offline_voice_map, ln.speaker)
        else:
            voice = character_voice_map.get(ln.speaker, default_voice)
        signature = clip_signature(ln.en, _voice_for_signature(clone, tts_engine, voice, exaggeration))
        clip_path = os.path.join(clips_dir, f"line_{ln.idx:04d}_{signature}.wav")

        clip = None
        # already-generated clips (e.g. from a prior partial run) are reused, not re-synthesized
        if os.path.exists(clip_path):
            try:
                clip = AudioSegment.from_file(clip_path)
            except Exception:
                pass  # corrupt leftover clip -- fall through and regenerate it
        if clip is None:
            try:
                if clone:
                    call_with_backoff(lambda: _synthesize_cloned(clone, ln.en, clip_path, exaggeration))
                elif tts_engine == "offline":
                    call_with_backoff(lambda: synthesize_line_offline(ln.en, voice, clip_path))
                else:
                    call_with_backoff(lambda: _synthesize_edge_tts_with_piper_fallback(
                        ln.en, voice, clip_path, offline_voice_map, ln.speaker))
                clip = AudioSegment.from_file(clip_path)
            except Exception as e:
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
# Chatterbox's own output length cap (about 40s per call) needs a smaller
# unit.
NARRATION_TTS_MAX_CHARS = 500
_ENGINE_TTS_MAX_CHARS = {"chatterbox": 300}
# build_narration_track's parallel generation: the first few missing clips
# run one at a time (a warm-up that surfaces a broken setup -- a blocked
# edge-tts, a bad key -- on a handful of calls instead of a whole pool's
# worth), then the rest in a thread pool. Only PARALLEL_SAFE_ENGINES ever
# reach the pool.
NARRATION_WARMUP_CLIPS = 3
NARRATION_MAX_WORKERS = 4

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


def _narration_steps(lines, character_clone_map, tts_engine, emotion_map, paragraph_ends):
    """Splits narration lines, in order, into ("blank", line) for a line
    with nothing to say and ("unit", unit) for one TTS call. A unit joins
    consecutive lines while they share a speaker (so one voice) and, for
    Chatterbox, the same emotional delivery, up to the engine's character
    budget -- never across a paragraph end or a chapter heading, which
    always stands alone so it can mark an audiobook chapter."""
    from emotion import chatterbox_exaggeration
    steps, current = [], None
    for ln in lines:
        text = ln.en.strip()
        if not text:
            steps.append(("blank", ln))
            current = None
            continue
        clone = character_clone_map.get(ln.speaker)
        engine = clone.get("engine", DEFAULT_CLONE_ENGINE) if clone else tts_engine
        exaggeration = chatterbox_exaggeration(emotion_map.get(ln.idx)) if engine == "chatterbox" else None
        heading = is_chapter_heading(ln)
        if (current is not None and not heading and current["speaker"] == ln.speaker
                and current["exaggeration"] == exaggeration
                and len(current["text"]) + 1 + len(text) <= _ENGINE_TTS_MAX_CHARS.get(
                    engine, NARRATION_TTS_MAX_CHARS)):
            current["lines"].append(ln)
            current["text"] += " " + text
        else:
            current = {"lines": [ln], "text": text, "speaker": ln.speaker, "clone": clone,
                       "engine": engine, "exaggeration": exaggeration, "error": None}
            steps.append(("unit", current))
        if heading or (paragraph_ends is not None and ln.idx in paragraph_ends):
            current = None
    return steps


def build_narration_track(lines, drama_dir: str, character_voice_map: dict,
                           default_voice: str = "en-US-AvaNeural", progress_cb=None,
                           character_clone_map: dict = None, gap_ms: int = 350,
                           tts_engine: str = "edge_tts", emotion_map: dict = None,
                           max_workers: int = NARRATION_MAX_WORKERS, offline_voice_map: dict = None):
    """
    For novel-narration mode: there's no pre-existing timing to sync
    to, so clips are generated and simply concatenated in order with a
    small gap between them. Mutates each line's .start/.end to the
    actual timing of its generated audio -- so you get a usable .srt
    alongside the narration audio.

    Each TTS call covers a unit of several consecutive lines where it
    can (see _narration_steps), so the audio has cross-sentence prosody
    while each line stays its own, shorter subtitle cue: a unit's clip
    time is divided back across its lines in proportion to their length
    (resegment.split_times, the same split re-segmentation uses), and
    every line in the unit points at the one shared clip.

    Clips are generated first -- any that already exist from a prior run
    are reused, not regenerated -- then assembled in order. Generation
    runs a short sequential warm-up, then a thread pool, for engines that
    can safely overlap (PARALLEL_SAFE_ENGINES); every other engine runs
    one clip at a time.

    Returns (path_to_wav, errors) -- a line whose unit failed is skipped
    (silent gap inserted instead) rather than aborting the whole narration.
    """
    from types import SimpleNamespace
    from pydub import AudioSegment
    from translate_engines import call_with_backoff
    from resegment import split_times

    clips_dir = os.path.join(drama_dir, "dub_clips")
    os.makedirs(clips_dir, exist_ok=True)
    character_clone_map = character_clone_map or {}
    emotion_map = emotion_map or {}
    errors = []

    steps = _narration_steps(lines, character_clone_map, tts_engine, emotion_map,
                             narration_paragraph_ends(lines, drama_dir))
    units = [unit for kind, unit in steps if kind == "unit"]
    for unit in units:
        first, last = unit["lines"][0].idx, unit["lines"][-1].idx
        voice = (offline_voice_for(offline_voice_map, unit["speaker"]) if tts_engine == "offline"
                 else character_voice_map.get(unit["speaker"], default_voice))
        signature = clip_signature(unit["text"], _voice_for_signature(
            unit["clone"], tts_engine, voice, unit["exaggeration"]))
        span = f"{first:04d}" if first == last else f"{first:04d}-{last:04d}"
        unit["clip_path"] = os.path.join(clips_dir, f"line_{span}_{signature}.wav")

    def synthesize(unit, clip_path):
        text, speaker = unit["text"], unit["speaker"]
        if unit["clone"] and unit["exaggeration"] is not None:
            _synthesize_cloned(unit["clone"], text, clip_path, unit["exaggeration"])
        elif unit["clone"]:
            _synthesize_cloned(unit["clone"], text, clip_path)
        elif tts_engine == "offline":
            synthesize_line_offline(text, offline_voice_for(offline_voice_map, speaker), clip_path)
        else:
            _synthesize_edge_tts_with_piper_fallback(
                text, character_voice_map.get(speaker, default_voice), clip_path,
                offline_voice_map, speaker)

    def generate(unit):
        """Runs in a pool thread for parallel-safe engines -- returns an
        error string instead of raising, so one failure can't stop the pool."""
        # Written under a temporary name and renamed once complete, so a
        # Cancel (which kills the process, possibly mid-write on several
        # pool threads at once) never leaves a partial clip behind for the
        # next run's "already exists" check to reuse.
        partial = unit["clip_path"][:-len(".wav")] + ".partial.wav"
        try:
            call_with_backoff(lambda: synthesize(unit, partial))
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

    pending = []
    for unit in units:
        if os.path.exists(unit["clip_path"]):
            record(unit, None)
        else:
            pending.append(unit)
    parallel = [u for u in pending if u["engine"] in PARALLEL_SAFE_ENGINES]
    one_at_a_time = ([u for u in pending if u["engine"] not in PARALLEL_SAFE_ENGINES]
                     + parallel[:NARRATION_WARMUP_CLIPS])
    for unit in one_at_a_time:
        record(unit, generate(unit))
    rest = parallel[NARRATION_WARMUP_CLIPS:]
    if rest:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(generate, unit): unit for unit in rest}
            for future in as_completed(futures):
                record(futures[future], future.result())

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
        cuts = (split_times(SimpleNamespace(start=start_ms, end=end_ms), [ln.en for ln in members])
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


def narration_chapters(lines, paragraph_ends=None) -> list:
    """[(start_seconds, title), ...] for an audiobook's chapter markers,
    from lines already timed by build_narration_track. The novel's own
    chapter headings when it has any; otherwise one chapter per paragraph
    of its source text; otherwise (source text missing or edited since)
    one per generated clip, which never crosses a paragraph end."""
    voiced = [ln for ln in lines if ln.en.strip()]
    if not voiced:
        return []
    headings = [ln for ln in voiced if is_chapter_heading(ln)]
    if headings:
        starts = headings if headings[0] is voiced[0] else [voiced[0]] + headings
    elif paragraph_ends is not None:
        starts, new_paragraph = [], True
        for ln in lines:
            if ln.en.strip() and new_paragraph:
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
        title = " ".join(ln.en.split())
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


def export_narration_m4b(lines, drama_dir: str, title: str = None, out_path: str = None) -> str:
    """Encodes narration_track.wav as an M4B audiobook (AAC) with chapter
    markers (narration_chapters). Needs the narration generated first --
    lines carrying the timing build_narration_track gave them -- and
    ffmpeg on PATH. Returns the .m4b path."""
    import subprocess
    wav_path = os.path.join(drama_dir, "narration_track.wav")
    if not os.path.exists(wav_path):
        raise FileNotFoundError("No narration_track.wav yet -- generate the narration first.")
    total_ms = _wav_duration_ms(wav_path) or int(max((ln.end for ln in lines), default=0.0) * 1000)
    chapters = narration_chapters(lines, narration_paragraph_ends(lines, drama_dir))
    meta_path = os.path.join(drama_dir, "narration_chapters.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(narration_ffmetadata(chapters, total_ms, title))
    out_path = out_path or os.path.join(drama_dir, "narration.m4b")
    cmd = ["ffmpeg", "-y", "-i", wav_path, "-i", meta_path, "-map", "0:a",
           "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "64k",
           "-f", "ipod", out_path]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def build_track_subprocess_worker(lines, drama_dir, character_voice_map, default_voice,
                                  character_clone_map, tts_engine, is_narration, emotion_map,
                                  max_speedup, max_slowdown, offline_voice_map, result_queue):
    """Step 4e: entry point for running build_dub_track()/build_narration_track()
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
    narration has no timing to fit, so it ignores them). offline_voice_map:
    each speaker's Piper voice, separate from character_voice_map's
    edge-tts names."""
    try:
        kwargs = dict(default_voice=default_voice, character_clone_map=character_clone_map,
                      tts_engine=tts_engine, emotion_map=emotion_map,
                      offline_voice_map=offline_voice_map)
        if is_narration:
            out_path, errors = build_narration_track(lines, drama_dir, character_voice_map, **kwargs)
        else:
            out_path, errors = build_dub_track(lines, drama_dir, character_voice_map, **kwargs,
                                               max_speedup=max_speedup, max_slowdown=max_slowdown)
        result_queue.put(("ok", {"lines": lines, "out_path": out_path, "errors": errors}))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))
