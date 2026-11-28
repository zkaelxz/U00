"""
services/listen_service.py -- the Reader's "Listen" for a narrated title: whether
a narration can be made, starting it, and the finished track. One voice engine,
the title's narration language, no pacing limits. The job (`dub_<id>`), its
process worker (dub_narration) and the track file (narration_track.flac, or the .wav of an older title) keep
the names the narration path always had, so existing jobs and tracks still work.

No FastAPI import: plain dicts out, no filesystem path.
"""
import functools
import importlib.util
import os
import shutil

import background_jobs
import db
from voice import dub
from voice import dub_narration
import novel_text
from voice import voice_venv
from jobs import job_launch
from services import characters_service, model_readiness_service, ownership_service, settings_service
from lib.errors import ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError

NARRATION_LANGUAGE_OPTIONS = ["translation", "original"]

TTS_ENGINES = [{"key": key, "label": label} for key, label in dub.CLONE_ENGINES.items()]

NO_ENGINE_INSTALLED_MESSAGE = "No voice engine is installed. Install one in Diagnostics."

_NOT_NARRATED = "Listen is only for narrated novels."
TEST_VOICE_DIR = "voice_test"
TEST_VOICE_NAME = "sample.wav"
TEST_VOICE_JOB_PREFIX = "voicetest_"
# A test is a taste of the voice, not a chapter: long lines are cut so it finishes quickly.
TEST_SENTENCE_MAX_CHARS = 120

# Python module and display name of each engine's package. A dub.VENV_ENGINES engine
# is never imported here: its module names the package inside its own venv.
_ENGINE_PACKAGES = {"omnivoice": ("omnivoice", "OmniVoice"), "qwen3_tts": ("qwen_tts", "Qwen3-TTS"),
                    "chatterbox": ("chatterbox", "Chatterbox")}


def _drama_path(drama_id: int) -> str:
    """The drama's folder path WITHOUT creating it (db.drama_dir makes the
    directory, which a read-only GET must never do)."""
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _require_narrated(drama_id: int) -> dict:
    drama = ownership_service.require_drama(drama_id)
    if drama.get("content_mode") != "novel_narration":
        raise NotFoundError(_NOT_NARRATED)
    return drama


def _narration_language(drama: dict) -> str:
    language = drama.get("narration_language") or "translation"
    return language if language in NARRATION_LANGUAGE_OPTIONS else "translation"


UNLABELLED_SPEAKER_NAME = "Lines with no speaker"


def _speakers_without_voice(characters, lines, engine: str, text_field: str, drama_path: str) -> list:
    """Display names of the speakers with text to speak that `engine` has no voice for. Only
    OmniVoice fills a bare speaker with a default voice; the other engines need a clip."""
    spoken = {ln.speaker or None for ln in lines if (getattr(ln, text_field) or "").strip()}
    clone_map = dub.clone_map_from_characters(characters, drama_path, default_engine=engine, speaker_labels=spoken)
    names = {c["speaker_label"]: c.get("character_name") or c["speaker_label"] for c in characters}
    return sorted(names.get(s) or s or UNLABELLED_SPEAKER_NAME for s in spoken if s not in clone_map)


def _no_voice_message(names: list, engine: str) -> str:
    return (f"{', '.join(names)} {'has' if len(names) == 1 else 'have'} no voice yet. "
            f"{dub.engine_label(engine)} needs a reference clip or a voice description for every speaker; "
            "only OmniVoice picks voices on its own.")


def get_listen_config(drama_id: int) -> dict:
    """What Listen needs to show: the engines (each with why it can't run here),
    the language options, a blocker that applies whatever engine is picked, how
    many lines are speakable, whether a track exists and whether the GPU is used.
    NotFoundError for an unknown title or one that is not a narrated novel."""
    drama = _require_narrated(drama_id)
    narration_language = _narration_language(drama)
    narrate_original = narration_language == "original"
    source_language = drama.get("source_language") or "zh"

    ddir = _drama_path(drama_id)
    lines = db.load_line_objects(drama_id)
    chars = db.list_characters_with_series_names(drama_id)
    speaker_labels = sorted({ln.speaker for ln in lines if ln.speaker})
    clone_map = dub.clone_map_from_characters(chars, ddir, speaker_labels=speaker_labels)

    text_field = "zh" if narrate_original else "en"
    return {
        "drama_id": drama_id,
        "narration_language": narration_language,
        "narration_language_options": list(NARRATION_LANGUAGE_OPTIONS),
        "tts_engines": [{**e, "unavailable_reason": _engine_unavailable_reason(e["key"], narrate_original,
                                                                               source_language),
                         "speakers_without_voice": _speakers_without_voice(chars, lines, e["key"], text_field, ddir)}
                        for e in TTS_ENGINES],
        "default_engine": dub.DEFAULT_CLONE_ENGINE,
        "blocker": config_blocker(chars),
        "gpu_required": dub.clone_map_uses_local_model(clone_map),
        "speakable_line_count": sum(1 for ln in lines if (getattr(ln, text_field) or "").strip()),
        "track_available": novel_text.find_narration_track(ddir) is not None,
    }


def _drama_file(drama_id: int, name: str, missing: str) -> dict:
    """{path (never returned to clients), name} for a file in the narrated title's folder;
    NotFoundError(missing) when it is absent (symlinks and escapes count as missing)."""
    _require_narrated(drama_id)
    root = _drama_path(drama_id)
    path = os.path.join(root, name)
    real_root = os.path.realpath(root)
    if (os.path.islink(path) or not os.path.isfile(path)
            or os.path.commonpath([real_root, os.path.realpath(path)]) != real_root):
        raise NotFoundError(missing)
    return {"path": path, "name": name}


def get_listen_track(drama_id: int) -> dict:
    """Server-side file lookup for the finished narration track: {path (never
    returned to clients), name}. NotFoundError for an unknown or non-narrated
    title, or when no track exists."""
    _require_narrated(drama_id)
    name = novel_text.find_narration_track(_drama_path(drama_id)) or novel_text.NARRATION_TRACK_NAMES[0]
    return _drama_file(drama_id, name, "No narration track available.")


def get_test_voice_clip(drama_id: int) -> dict:
    """The last test sentence, as get_listen_track. The folder holds one clip, replaced by each test."""
    return _drama_file(drama_id, os.path.join(TEST_VOICE_DIR, TEST_VOICE_NAME), "No voice test available.")


def _engine_install_problem(engine: str):
    """Fixed-text reason `engine` can't run on this PC, or None. Never names
    a path."""
    module, label = _ENGINE_PACKAGES[engine]
    if engine in dub.VENV_ENGINES:
        return None if voice_venv.is_ready(engine) else f"{label} is not installed. Install it in Diagnostics."
    if importlib.util.find_spec(module) is None:
        return f"{label} is not installed. Install it in Diagnostics."
    return None


def engine_install_problem(engine: str):
    """Public name of the install check, for the self-test."""
    return _engine_install_problem(engine)


def _pydub_problem():
    """The narration track is joined with pydub, an opt-in package: without this check the
    run fails later with a raw "No module named" error."""
    if importlib.util.find_spec("pydub") is None:
        return ('Joining the narration needs the pydub package. '
                'Install the pydub package in Diagnostics > Packages.')
    return None


def _missing_engine_dependency(tts_engine: str):
    """Fixed-text reason the requested engine (or ffmpeg) can't run here, or
    None. Never names a path."""
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed or not on PATH, which narration requires."
    return _pydub_problem() or _engine_install_problem(tts_engine)


def _engine_unavailable_reason(engine: str, narrate_original: bool, source_language: str):
    """_missing_engine_dependency plus, for original-language narration, the
    engines not confirmed to speak that language."""
    missing = _missing_engine_dependency(engine)
    if missing:
        return missing
    if narrate_original and not dub.clone_engine_supports_language(engine, source_language):
        return (f"{_ENGINE_PACKAGES[engine][1]} can't speak this title's original language. "
                "Narrate the translation instead.")
    return None


def config_blocker(characters):
    """Plain reason Listen can't run whatever the engine picked: no engine
    installed at all, or a character stored with a removed engine. None when
    the choice of engine decides it."""
    removed = dub.engine_blockers(characters, dub.DEFAULT_CLONE_ENGINE)
    if removed:
        return " ".join(removed)
    if all(_engine_install_problem(e["key"]) for e in TTS_ENGINES):
        return NO_ENGINE_INSTALLED_MESSAGE
    return None


def require_can_generate(tts_engine: str, characters, narrate_original: bool = False,
                         source_language: str = "zh") -> None:
    """Refuses a run that cannot work, in this order: a removed or unknown
    engine (the run's or a character's) is InvalidInputError; no engine
    installed, or the picked one missing/unable to speak the original
    language, is DependencyUnavailableError."""
    blockers = dub.engine_blockers(characters, tts_engine)
    if blockers:
        raise InvalidInputError(" ".join(blockers))
    reason = _engine_unavailable_reason(tts_engine, narrate_original, source_language)
    if reason:
        raise DependencyUnavailableError(reason)


def apply_narration_result(drama_id: int, result: dict, clips_at_start=None) -> None:
    """on_done hook body: persists a finished narration run -- field-scoped
    save_lines(dub_filename, start, end) and status "dubbed". The subprocess's
    returned lines are only a source of those fields, matched by permanent line
    id onto the CURRENT database lines, so edits made while the job ran (text,
    flag, flag_note, speaker) are never overwritten and a line merged away is
    skipped. A run whose lines were all replaced meanwhile (Chunk & tag) matches
    nothing and leaves the title's status alone: "dubbed" would hide the new
    lines from the translate flow."""
    if db.get_drama(drama_id) is None:
        return
    _keep_clip_transcripts(drama_id, (result or {}).get("clip_transcripts") or {}, clips_at_start or {})
    lines = [ln for ln in (result or {}).get("lines") or [] if ln.id is not None]
    failed = {e.get("line_idx") for e in (result or {}).get("errors") or []}
    # A failed unit made no clip, so a name left from an earlier run would point at stale audio.
    times = {ln.id: (ln.start, ln.end, None if ln.idx in failed else ln.dub_filename) for ln in lines}
    if not _save_narration_times(drama_id, times):
        return
    if failed and not any(clip for _s, _e, clip in times.values()):
        return  # nothing was narrated; "dubbed" would claim a track that is only silence
    db.update_drama(drama_id, status="dubbed")
    _remove_unused_clips(drama_id)


def _clip_whisper():
    """The Whisper a clip's transcript is written with ({"local_model_path"}), or None when it is
    not on this PC: the Settings folder as transcription uses it, else the cached large-v3."""
    local = settings_service.get("whisper_model_path") or None
    if not model_readiness_service.whisper_ready(dub_narration.CLIP_TRANSCRIPT_WHISPER, local):
        return None
    return {"local_model_path": local}


def _keep_clip_transcripts(drama_id: int, transcripts: dict, clips_at_start: dict) -> None:
    """Saves the transcript Whisper wrote for a speaker's clip, so later runs and Voices use it
    (and it can be corrected there). Only while the speaker still has the clip it was heard
    from and no transcript: one typed or a clip changed during the run wins."""
    rows = {c["speaker_label"]: c for c in db.list_characters(drama_id)}
    for label, text in transcripts.items():
        row = rows.get(label)
        if (row and row.get("ref_audio_filename") and row.get("ref_audio_filename") == clips_at_start.get(label)
                and not (row.get("ref_text") or "").strip()):
            db.upsert_character(drama_id, label, ref_text=text[:characters_service.MAX_REF_TEXT_LEN])


def _save_narration_times(drama_id: int, times: dict) -> bool:
    """Saves (start, end, clip) by permanent line id onto the title's current lines, so edits
    made while the run went are kept and a line merged away is skipped. Whether any matched."""
    current = db.load_line_objects(drama_id)
    matched = False
    for ln in current:
        if ln.id in times:
            matched = True
            ln.start, ln.end, ln.dub_filename = times[ln.id]
    if matched:
        db.save_lines(drama_id, current, fields=("dub_filename", "start", "end"))
    return matched


def apply_narration_part(drama_id: int, job_id: str, payload: dict) -> None:
    """on_partial hook body: the run has joined the part spoken so far into the title's track,
    so its lines get that track's times now, and the job's result says how many lines can be
    played (the Reader reloads its player when that grows). The status stays as it was: the
    title is not "dubbed" until the whole track is made."""
    if db.get_drama(drama_id) is None:
        return
    rows = (payload or {}).get("times") or []
    _save_narration_times(drama_id, {line_id: (start, end, clip) for line_id, start, end, clip in rows})
    background_jobs.set_result(job_id, {"playable_lines": sum(1 for row in rows if row[3])}, mirror=True)


def _remove_unused_clips(drama_id: int) -> None:
    """Deletes the clips no line of the title names any more (each voice or text change
    leaves the old ones, about 170 MB per narrated hour). Read back after the save, so a
    line edited or added while the run went keeps its clip. Never fails the run."""
    try:
        used = [row.get("dub_filename") for row in db.load_lines(drama_id)]
        removed = dub_narration.remove_unused_clips(db.drama_dir(drama_id), used)
    except Exception as exc:
        import applog
        applog.get_logger().warning(f"unused narration clips not removed on title {drama_id}: "
                                    f"{type(exc).__name__}")
        return
    if removed:
        import applog
        applog.get_logger().info(f"removed {removed} unused narration clips on title {drama_id}")


def _narration_pace() -> float:
    pace = settings_service.get("narration_pace")
    return float(pace) if isinstance(pace, (int, float)) else 1.0


def narration_job_id(drama_id: int) -> str:
    # Kept as dub_<id> so jobs started before the Dub stage was removed still resolve.
    return f"dub_{drama_id}"


def start_listen(drama_id: int, tts_engine: str, narration_language=None) -> dict:
    """Starts the narration track as a background process job (`dub_<id>`);
    the result is applied by an on_done hook. {"job_id"}. NotFoundError (unknown
    or non-narrated title), InvalidInputError (unknown engine or language, a
    removed engine, nothing to narrate, a speaker with no voice), DependencyUnavailableError (ffmpeg or
    the engine package missing), ConflictError (already running)."""
    drama = _require_narrated(drama_id)
    if not tts_engine:
        raise InvalidInputError("No voice engine is available.")
    if narration_language is not None and narration_language not in NARRATION_LANGUAGE_OPTIONS:
        raise InvalidInputError("Unknown narration language.")
    if narration_language is None:
        narration_language = _narration_language(drama)
    narrate_original = narration_language == "original"
    chars = db.list_characters_with_series_names(drama_id)
    source_lang = drama.get("source_language") or "zh"
    require_can_generate(tts_engine, chars, narrate_original, source_lang)
    lines = db.load_line_objects(drama_id)
    if not any((getattr(ln, "zh" if narrate_original else "en") or "").strip() for ln in lines):
        raise InvalidInputError("No source text to narrate." if narrate_original
                                else "No translated lines to narrate yet.")

    job_id = narration_job_id(drama_id)
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(_ALREADY_RUNNING)

    voiceless = _speakers_without_voice(chars, lines, tts_engine, "zh" if narrate_original else "en",
                                        _drama_path(drama_id))
    if voiceless:
        raise InvalidInputError(_no_voice_message(voiceless, tts_engine))

    ddir = db.drama_dir(drama_id)
    speaker_labels = {ln.speaker or None for ln in lines}
    clone_map = dub.clone_map_from_characters(
        chars, ddir, default_engine=tts_engine, speaker_labels=speaker_labels)
    # Keyword-bound so background_jobs' trailing result_queue lands on the
    # worker's result_queue parameter (declared before these options).
    worker = functools.partial(dub_narration.build_narration_subprocess_worker,
                               narrate_original=narrate_original, source_language=source_lang,
                               pace=_narration_pace(), use_gpu=bool(settings_service.get("use_gpu")),
                               clip_whisper=_clip_whisper())
    clips_at_start = {c["speaker_label"]: c.get("ref_audio_filename") for c in chars}
    started = job_launch.start_process_job(
        job_id, worker, args=(lines, ddir, clone_map),
        gpu_touching=dub.clone_map_uses_local_model(clone_map),
        description=f"Narration (title #{drama_id})",
        kill_whole_tree=True, start_method="spawn",
        on_done=lambda _jid, result: apply_narration_result(drama_id, result, clips_at_start),
        on_partial=lambda jid, payload: apply_narration_part(drama_id, jid, payload),
        # A cancel kills the worker before it can remove its half-written track.
        on_finish=lambda _jid: dub_narration.remove_partial_track(ddir),
        line_fields=("dub_filename", "start", "end"))
    if not started:
        raise ConflictError(_ALREADY_RUNNING)
    # Saved only now, so a refused start leaves the title as it was.
    if narration_language != (drama.get("narration_language") or "translation"):
        db.update_drama(drama_id, narration_language=narration_language)
    if tts_engine == "omnivoice":
        # The clone map already holds these picks: a saved description maps to the same voice.
        _keep_default_voices(drama_id, chars, speaker_labels)
    return {"job_id": job_id}


_ALREADY_RUNNING = "A narration is already running for this title."


def _keep_default_voices(drama_id: int, chars, speaker_labels) -> bool:
    """Saves the voice OmniVoice picks for each speaker with none, on the title's character
    and on its linked series character, so it stays the same in later chapters and titles.
    Lines with no speaker have no character to keep it on. Whether anything was saved."""
    linked = {c["speaker_label"]: c.get("series_character_id") for c in chars}
    picks = {label: design for label, design in dub.default_voice_designs(chars, speaker_labels).items()
             if label}
    for label, design in picks.items():
        db.upsert_character(drama_id, label, voice_design=design)
        if linked.get(label):
            db.remember_series_character_voice_design(linked[label], design)
    return bool(picks)


def start_test_voice(drama_id: int, speaker_label: str, tts_engine: str, narration_language=None) -> dict:
    """Starts job `voicetest_<id>`: speaks the speaker's first line (cut short) in the voice a
    full run would give them, so it can be heard before the whole narration is made. The clip is
    read with get_test_voice_clip. {"job_id"}. Raises as start_listen does, plus NotFoundError
    for a speaker with no text to speak."""
    drama = _require_narrated(drama_id)
    if not tts_engine:
        raise InvalidInputError("No voice engine is available.")
    if narration_language is not None and narration_language not in NARRATION_LANGUAGE_OPTIONS:
        raise InvalidInputError("Unknown narration language.")
    narrate_original = (narration_language or _narration_language(drama)) == "original"
    source_lang = drama.get("source_language") or "zh"
    chars = db.list_characters_with_series_names(drama_id)
    require_can_generate(tts_engine, chars, narrate_original, source_lang)

    text_field = "zh" if narrate_original else "en"
    spoken = [(ln.speaker or None, (getattr(ln, text_field) or "").strip()) for ln in db.load_line_objects(drama_id)]
    spoken = [(who, text) for who, text in spoken if text]
    sentence = next((text for who, text in spoken if who == speaker_label), None)
    if sentence is None:
        raise NotFoundError("This speaker has no text to speak.")
    # Every speaker with text is passed so a default voice is the one the full run gives this speaker.
    clone_map = dub.clone_map_from_characters(chars, db.drama_dir(drama_id), default_engine=tts_engine,
                                              speaker_labels={who for who, _ in spoken})
    voice = clone_map.get(speaker_label)
    if voice is None:
        names = {c["speaker_label"]: c.get("character_name") or c["speaker_label"] for c in chars}
        raise InvalidInputError(_no_voice_message([names.get(speaker_label) or speaker_label], tts_engine))

    job_id = f"{TEST_VOICE_JOB_PREFIX}{drama_id}"
    started = job_launch.start_job(
        job_id, _test_voice_job, drama_id, voice, sentence[:TEST_SENTENCE_MAX_CHARS],
        source_lang if narrate_original else "en",
        gpu_touching=dub.clone_map_uses_local_model({speaker_label: voice}),
        description=f"Voice test (title #{drama_id})", line_fields=())
    if not started:
        raise ConflictError(f"A voice test is already running for title {drama_id}.")
    return {"job_id": job_id}


def _test_voice_job(drama_id: int, voice: dict, sentence: str, language: str) -> None:
    folder = os.path.join(db.drama_dir(drama_id), TEST_VOICE_DIR)
    os.makedirs(folder, exist_ok=True)
    error = dub_narration.speak_sample(sentence, voice, os.path.join(folder, TEST_VOICE_NAME), language)
    if error:
        raise RuntimeError(error)
