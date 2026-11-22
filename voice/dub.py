"""
dub.py -- the voice layer shared by novel narration and voice cloning: the
one voice engine, per-speaker clone maps, reference-clip extraction and the
helpers that decide what a speaker's voice is. dub_narration.py builds the
narration track on top of it, speaking the translation or, via
narrate_original, the drama's own source-language text.

Voices come from OmniVoice, Qwen3-TTS or Chatterbox (see CLONE_ENGINES); each
runs in a venv of its own (voice_venv). Reference clips can be cut per speaker from the original audio
(services/voice_clone_service.py) or set manually. A speaker with no clip gets a
designed voice from a plain description. build_narration_track() takes a
character_clone_map (clone_map_from_characters() builds it, one entry per
speaker) and speaks each line with that speaker's entry.
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
    "qwen3_tts": "Qwen3-TTS (clone from a clip)",
    "chatterbox": "Chatterbox (clone from a clip)",
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
    # Qwen's model card lists Chinese, Japanese and Korean among its ten languages.
    "qwen3_tts": {"zh", "ja", "ko"},
    # chatterbox.mtl_tts.SUPPORTED_LANGUAGES (23 languages) includes all three.
    "chatterbox": {"zh", "ja", "ko"},
}


def clone_engine_supports_language(engine: str, language: str) -> bool:
    """Whether `engine` is confirmed to generate `language` well. An engine
    not in CLONE_ENGINE_ORIGINAL_LANGUAGES (a removed one) isn't gated here
    -- always True; engine_refusal is what stops it."""
    if engine not in CLONE_ENGINE_ORIGINAL_LANGUAGES:
        return True
    return language in CLONE_ENGINE_ORIGINAL_LANGUAGES[engine]


# Engines that load a local model -- dub generation takes the GPU slot for
# these. One model on one GPU gains nothing from threads; the worker speaks
# two lines of one voice per model call instead (voice_worker.BATCH_SIZES).
LOCAL_MODEL_ENGINES = {"omnivoice", "qwen3_tts", "chatterbox"}
# Engines whose packages clash with the app's, run through voice_venv. Their lines are
# spoken in one batch per run so the model loads once.
VENV_ENGINES = {"omnivoice", "qwen3_tts", "chatterbox"}
# The first run downloads the model (a few GB) before speaking anything.
VENV_LOAD_SECONDS = 1800
VENV_SECONDS_PER_LINE = 180
VENV_LINE_FAILED = "The voice engine could not speak this line."


def _cuda_available() -> bool:
    import torch
    return torch.cuda.is_available()


def _check_vram(model_name):
    """Refuse a GPU load that clearly won't fit, with a
    plain message, before the model starts loading."""
    import vram
    vram.check_fits(model_name)


OMNIVOICE_REPO = "k2-fsa/OmniVoice"


def engine_label(engine: str) -> str:
    return CLONE_ENGINES[engine].split(" (")[0]


def _engine_start_failed(engine: str) -> str:
    return f"The {engine_label(engine)} engine could not start. Check it in Diagnostics."


def speak_in_venv(engine: str, requests: list, workdir: str):
    """Speaks `requests` ({"text", "out", "language", "ref_audio", "ref_text", "instruct"}) with a
    VENV_ENGINES engine in one child. Yields (index, error) once per request, error None
    when its file was written, in the order they finish. Error texts are fixed
    sentences; the engine's own text is only logged, as it can name local paths."""
    import applog
    from voice import voice_venv
    from engine_backends.shared import redact_secrets
    from memory_headroom import HeadroomError
    try:
        cuda = _cuda_available()
    except ImportError:
        # The app's own torch is optional; the engine's torch lives in its venv.
        cuda = False
    try:
        if cuda:
            _check_vram(engine_label(engine))
    except HeadroomError:
        raise
    except Exception as exc:
        # The VRAM refusal is plain text that names no path.
        for i in range(len(requests)):
            yield i, str(exc)
        return
    items = [{"id": str(i), "text": r["text"], "out": r["out"], "language": r["language"],
              "ref_audio": r.get("ref_audio"), "ref_text": r.get("ref_text") or "",
              "instruct": r.get("instruct") or ""}
             for i, r in enumerate(requests)]
    if not voice_venv.is_ready(engine):
        for i in range(len(requests)):
            yield i, _engine_start_failed(engine)
        return
    fatal = None
    # own_group=False: this runs inside the narration job worker, and a cancel kills that
    # worker's whole group.
    for event in voice_venv.run_batch(
            engine, items, workdir,
            timeout=VENV_LOAD_SECONDS + VENV_SECONDS_PER_LINE * len(items), own_group=False):
        if event.get("detail"):
            applog.get_logger().warning("%s: %s", engine_label(engine),
                                        redact_secrets(event["detail"]))
        if "fatal" in event:
            fatal = _engine_start_failed(engine)
        elif "done" in event:
            yield int(event["done"]), None
        elif "failed" in event:
            yield int(event["failed"]), VENV_LINE_FAILED
        elif "unanswered" in event:
            for i in event["unanswered"]:
                yield int(i), fatal or VENV_LINE_FAILED


def clone_map_from_characters(characters, drama_dir: str,
                              default_engine: str = DEFAULT_CLONE_ENGINE,
                              speaker_labels=()) -> dict:
    """{speaker_label: clone entry} for build_narration_track,
    from db.list_characters() rows -- used by the Workspace tab to route every speaker.
    Per character, first match wins (the engine is the character's
    clone_engine, else default_engine, the run's choice):
      1. a reference clip, cloned with that engine;
      2. a voice description (OmniVoice voice design, no clip needed).
    A character whose engine was removed or is unknown gets no entry (see
    engine_blockers, which the caller checks before generating).
    speaker_labels: every speaker in the lines (None for lines with no
    speaker). One with no entry yet gets a designed voice when default_engine
    is OmniVoice (default_voice_designs). A character's elevenlabs_voice_id (hosted
    cloning, since removed) is ignored."""
    out = _own_voices(characters, drama_dir, default_engine)
    if default_engine == "omnivoice":
        for label, design in _picked_designs(characters, out, speaker_labels).items():
            out[label] = {"engine": "omnivoice", "instruct": design}
    return out


def default_voice_designs(characters, speaker_labels) -> dict:
    """{speaker_label: description} for each speaker an OmniVoice run would give a default
    voice: the one remembered on its linked series character (a row's series_voice_design,
    from db.list_characters_with_series_names), else a description no other speaker of the
    title uses. The caller saves them on the characters, so a speaker keeps its voice when
    later chapters bring new speakers instead of every voice moving along the list."""
    return _picked_designs(characters, _own_voices(characters, "", "omnivoice"), speaker_labels)


def _picked_designs(characters, own: dict, speaker_labels) -> dict:
    remembered = {c["speaker_label"]: (c.get("series_voice_design") or "").strip() for c in characters}
    bare = sorted((s for s in set(speaker_labels) if s not in own), key=lambda s: s or "")
    # Checked, as a restored backup can bring any text; a refused one would silence the speaker.
    picks = {s: remembered[s] for s in bare
             if remembered.get(s) and voice_description_problem(remembered[s]) is None}
    taken = {e["instruct"] for e in own.values() if e.get("instruct")} | set(picks.values())
    pool = [d for d in DEFAULT_VOICE_DESCRIPTIONS if d not in taken] or DEFAULT_VOICE_DESCRIPTIONS
    for i, label in enumerate(s for s in bare if s not in picks):
        picks[label] = pool[i % len(pool)]
    return picks


def _own_voices(characters, drama_dir: str, default_engine: str) -> dict:
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
    return out


# The words OmniVoice's voice design accepts (omnivoice 0.2.1, utils/voice_design.py),
# one set per group it allows only one of. Anything else, such as "warm audiobook
# narrator", makes OmniVoice refuse every line, which would leave the whole narration
# silent, so a description is checked before a run starts.
OMNIVOICE_DESCRIPTION_GROUPS = (
    {"male", "female", "男", "女"},
    {"child", "teenager", "young adult", "middle-aged", "elderly", "儿童", "少年", "青年", "中年", "老年"},
    {"very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch",
     "极低音调", "低音调", "中音调", "高音调", "极高音调"},
    {"whisper", "耳语"},
    {f"{a} accent" for a in ("american", "british", "australian", "chinese", "canadian", "indian",
                             "korean", "portuguese", "russian", "japanese")},
    {"河南话", "陕西话", "四川话", "贵州话", "云南话", "桂林话", "济南话", "石家庄话", "甘肃话",
     "宁夏话", "青岛话", "东北话"},
)
DESCRIPTION_HELP = ("A voice description is a few of these, separated by commas: male or female; child, "
                    "teenager, young adult, middle-aged or elderly; very low, low, moderate, high or very "
                    "high pitch; whisper; an accent such as british accent. For a warmer, audiobook-like "
                    "voice, give the speaker a reference clip of a narrator instead.")


def voice_description_problem(text: str):
    """Why OmniVoice would refuse `text` as a voice description, else None."""
    items = [i.strip().lower() for i in re.split(r"[,，]", text or "") if i.strip()]
    unknown = [i for i in items if not any(i in group for group in OMNIVOICE_DESCRIPTION_GROUPS)]
    if unknown:
        return f"OmniVoice doesn't know \u201c{', '.join(unknown)}\u201d. {DESCRIPTION_HELP}"
    if any(sum(i in group for i in items) > 1 for group in OMNIVOICE_DESCRIPTION_GROUPS):
        return f"The voice description names two of the same kind (two ages, say). {DESCRIPTION_HELP}"
    if any(i in OMNIVOICE_DESCRIPTION_GROUPS[4] for i in items) and any(
            i in OMNIVOICE_DESCRIPTION_GROUPS[5] for i in items):
        return f"A voice description can't have both an accent and a Chinese dialect. {DESCRIPTION_HELP}"
    return None


def engine_blockers(characters, tts_engine) -> list:
    """Plain messages for everything stored that can no longer generate: the
    run's own engine, each character's clone_engine, and a voice description
    OmniVoice would refuse (only where it is used: a speaker with no clip).
    Empty means nothing stands in the way. The rows themselves are never touched."""
    out = []
    refusal = engine_refusal(tts_engine)
    if refusal:
        out.append(refusal)
    for c in characters:
        name = c.get("character_name") or c["speaker_label"]
        engine = c.get("clone_engine")
        if engine and engine_refusal(engine):
            out.append(f"{name}: {engine_refusal(engine)}")
        design = (c.get("voice_design") or "").strip()
        if design and not c.get("ref_audio_filename") and voice_description_problem(design):
            out.append(f"{name}: {voice_description_problem(design)}")
    return out


def clone_map_uses_local_model(character_clone_map: dict) -> bool:
    """Whether generating with this map needs the GPU slot."""
    return any(v.get("engine") in LOCAL_MODEL_ENGINES
               for v in (character_clone_map or {}).values())


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


_DIGESTS: dict = {}


def _file_digest(path: str):
    """SHA-1 of the file's bytes; None when it can't be read (the engine then refuses the
    line itself). Cached by size and modification time, as every unit of a speaker asks."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _DIGESTS:
        digest = hashlib.sha1()
        try:
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    digest.update(block)
        except OSError:
            return None
        _DIGESTS[key] = digest.hexdigest()
    return _DIGESTS[key]


def _voice_for_signature(clone, lang=None) -> dict:
    """lang: the narration language mode ("zh"/"ja"/"ko" for
    original-language narration, omitted/None for ordinary translation-mode
    narration or dubbing) -- folded into the signature explicitly so
    switching a drama's narration language always regenerates its clips,
    rather than relying on the spoken text alone happening to differ.

    The reference clip counts by its content, not its path: "Replace clip" in the
    voice bank writes the new clip under the same file name, and a restored title
    has the same clip under a new folder."""
    out = dict(clone)
    if out.get("ref_audio"):
        out["ref_audio"] = _file_digest(out["ref_audio"])
    if lang:
        out["narration_lang"] = lang
    return out


# Encoding a whole narration track; same ceiling the export jobs use.
M4B_ENCODE_TIMEOUT_SECONDS = 4 * 3600


NO_VOICE_ERROR = "This speaker has no voice set up. Upload or extract a clip, or describe a voice."
