"""
diarize.py -- speaker diarization: figures out WHO is speaking when, so
lines can be grouped by character. Uses pyannote.audio, which needs:

  1. `pip install pyannote.audio soundfile`
  2. A free Hugging Face account + token: https://huggingface.co/settings/tokens
  3. Accepting the model terms at:
     https://huggingface.co/pyannote/speaker-diarization-community-1
     (and, as a fallback if that can't load,
     https://huggingface.co/pyannote/speaker-diarization-3.1)

This requires internet access on YOUR machine (to download the model
the first time) and works better with a GPU, but runs on CPU too --
just slower. Not run inside this sandbox since it has no network; the
code is here for you to run locally.

pyannote.audio 4.x needs Python 3.10+. Its pipeline(audio) call also
returns a different result type than 3.x -- see the getattr() in
diarize() below for why that's handled rather than assumed away.

diarize() pre-loads the audio with soundfile.read() and passes pyannote
a {"waveform", "sample_rate"} dict rather than a bare file path -- a
bare path makes pyannote.audio 4.x decode it through torchcodec, which
this app never installs. soundfile (libsndfile-based) reads it instead:
every audio_path reaching diarize() is always this app's own normalized
audio.wav (see core.extract_audio_from_video/extract_audio_slice, both
plain 16kHz mono PCM WAV), which soundfile handles directly with no
compiled-per-FFmpeg-version binary of its own -- unlike torchcodec, and
more robust on Windows than pinning torchaudio to an older release would
be, since torchaudio's own audio-loading path is being phased out
upstream. word_align.py's separate, legitimate use of torchaudio (for
Meta's MMS forced-alignment model, which needs the real thing) is
untouched.
"""

import datetime
import json
import os

DIARIZATION_MODELS = ("pyannote/speaker-diarization-community-1",
                      "pyannote/speaker-diarization-3.1")
TURNS_FILE = "diarization_turns.json"


def _from_pretrained(Pipeline, model: str, hf_token: str):
    try:
        # pyannote.audio 3.1+ renamed this kwarg from use_auth_token to
        # token (following huggingface_hub's own rename) and newer
        # releases reject use_auth_token outright with a TypeError rather
        # than just deprecation-warning on it.
        return Pipeline.from_pretrained(model, token=hf_token)
    except TypeError:
        # Older pyannote.audio installs (pre-3.1) don't accept `token`
        # either -- fall back to the name they actually expect.
        return Pipeline.from_pretrained(model, use_auth_token=hf_token)


def load_pipeline(hf_token: str):
    """(pipeline, model_name): community-1 first, 3.1 if it can't load --
    e.g. an older pyannote.audio that predates it, or its terms not
    accepted on this Hugging Face account yet. Raises the last error if
    neither loads."""
    from pyannote.audio import Pipeline
    last_error = None
    for model in DIARIZATION_MODELS:
        try:
            pipeline = _from_pretrained(Pipeline, model, hf_token)
            if pipeline is not None:  # 3.x returns None instead of raising on a gated model
                return pipeline, model
            last_error = RuntimeError(f"{model} returned no pipeline (terms not accepted?)")
        except Exception as exc:
            last_error = exc
    raise last_error


def validate_speaker_hints(num_speakers=None, min_speakers=None, max_speakers=None):
    """Step 105: checks the speaker-count hints and returns the cleaned
    (num_speakers, min_speakers, max_speakers), each None when unset.
    0/None means "not set" for all three (0 is the existing "auto-detect"
    value for the exact count). An exact count and a range are mutually
    exclusive -- pyannote lets num_speakers override the range, so
    accepting both would silently ignore half of what the user typed.
    Raises ValueError with a plain-English message on a bad combination."""
    num = int(num_speakers) if num_speakers else None
    lo = int(min_speakers) if min_speakers else None
    hi = int(max_speakers) if max_speakers else None
    for name, value in (("Exact speaker count", num), ("Minimum speakers", lo),
                        ("Maximum speakers", hi)):
        if value is not None and value < 1:
            raise ValueError(f"{name} must be at least 1.")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Minimum speakers can't be more than maximum speakers.")
    if num is not None and (lo is not None or hi is not None):
        raise ValueError("Use either an exact speaker count or a min/max range, not both.")
    return num, lo, hi


def select_device(use_gpu: bool = False) -> str:
    """Step 101: "cuda" when use_gpu is on and torch sees a CUDA device,
    else "cpu". Never raises -- a torch without CUDA support means CPU."""
    if not use_gpu:
        return "cpu"
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _place_pipeline(pipeline, use_gpu: bool) -> str:
    """Step 101: moves the loaded pyannote pipeline onto the GPU when
    use_gpu is on and CUDA is available. pyannote's own docs require an
    explicit pipeline.to(torch.device("cuda")); without it the pipeline
    stays on CPU even inside a job tagged gpu_touching. Returns the device
    actually used ("cuda" or "cpu"), falling back to "cpu" (logged) if the
    move itself fails."""
    device = select_device(use_gpu)
    if device == "cpu":
        return "cpu"
    try:
        import torch
        pipeline.to(torch.device(device))
    except Exception as exc:
        import applog
        applog.get_logger().error(f"diarization: moving pyannote to {device} failed, "
                                  f"running on CPU instead: {exc}")
        return "cpu"
    return device


def _run_pipeline(pipeline, audio, hints: dict, on_progress=None):
    """Calls the pipeline, passing pyannote's progress hook when on_progress
    is given. Progress is capped below 1.0 (the job isn't done until its
    result is applied) and never moves backwards across pyannote's steps. A
    pyannote without hook support is simply run without one."""
    if on_progress is None:
        return pipeline(audio, **hints)
    best = [0.08]

    def hook(step_name, step_artifact=None, file=None, total=None, completed=None, **_):
        try:
            label = str(step_name).replace("_", " ")
            if total and completed is not None and total > 0:
                frac = 0.08 + 0.87 * min(max(float(completed) / float(total), 0.0), 1.0)
                best[0] = max(best[0], frac)
                msg = f"Detecting speakers: {label} ({int(completed)} of {int(total)})"
            else:
                msg = f"Detecting speakers: {label}"
            on_progress(min(best[0], 0.95), msg)
        except Exception:
            pass  # progress is cosmetic; it must never fail the run

    try:
        return pipeline(audio, hook=hook, **hints)
    except TypeError as exc:
        if "hook" not in str(exc):
            raise
        return pipeline(audio, **hints)


OOM_FALLBACK_MESSAGE = ("Speaker detection ran out of GPU memory and is running on CPU, "
                        "this will be slower")
# Past tense, fixed text: for a finished run's result and the CLI summary.
OOM_FALLBACK_DONE_MESSAGE = ("Speaker detection ran out of GPU memory and ran on CPU, "
                             "which is slower.")
# The speaker model could not be moved onto the GPU at all (driver or CUDA
# build problem); the reason goes to the log, not into this fixed text.
PLACEMENT_FALLBACK_DONE_MESSAGE = ("Speaker detection ran on the CPU because the speaker "
                                   "model couldn't be moved to the GPU. This was slower than "
                                   "on the GPU.")


def is_cuda_oom(exc: BaseException) -> bool:
    """True for a CUDA out-of-memory failure: torch.cuda.OutOfMemoryError, or
    the plain RuntimeError older/other torch builds raise with the same text."""
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _free_gpu_memory() -> None:
    """Best effort: collect the dropped pipeline and hand cached CUDA blocks
    back so the CPU retry (and whatever else shares the card) can use them."""
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def diarize(audio_path: str, hf_token: str, num_speakers: int = None, return_model: bool = False,
           return_embeddings: bool = False, use_gpu: bool = False,
           min_speakers: int = None, max_speakers: int = None, run_info: dict = None,
           on_progress=None):
    """
    Returns a list of {"start": float, "end": float, "speaker": str}
    covering who spoke when, e.g. "SPEAKER_00", "SPEAKER_01", ... --
    or (segments, model_name) with return_model=True, or additionally
    (..., embeddings) with return_embeddings=True too -- see
    extract_speaker_embeddings() below. Both extra flags default off, so
    every existing call keeps its exact current return shape.

    use_gpu (Step 101): place the pipeline on CUDA when available.
    min_speakers/max_speakers (Step 105): a speaker-count range passed to
    pyannote's own min_speakers/max_speakers; mutually exclusive with
    num_speakers (validate_speaker_hints). run_info: an optional dict this
    fills with {"device": "cuda"|"cpu"}, the device actually used, plus
    "fell_back_to_cpu": True, "fallback_kind" ("oom" or "placement") and
    "fallback_reason" (short, only after a fallback; secrets redacted). A CUDA
    out-of-memory during the run is retried once on CPU (loudly, via on_progress and the log); the device selection is
    otherwise unchanged. If the CPU retry fails too, RuntimeError.
    on_progress: optional on_progress(fraction 0-1, message), called as the
    stages change and (where pyannote's hook reports it) as each step advances.
    """
    _say = on_progress or (lambda frac, message: None)
    num_speakers, min_speakers, max_speakers = validate_speaker_hints(
        num_speakers, min_speakers, max_speakers)
    _say(0.02, "Loading speaker model...")
    pipeline, model = load_pipeline(hf_token)
    device = _place_pipeline(pipeline, use_gpu)
    if run_info is not None:
        run_info["device"] = device
        if device == "cpu" and use_gpu and select_device(use_gpu) == "cuda":
            run_info.update(fell_back_to_cpu=True, fallback_kind="placement",
                            fallback_reason="Couldn't move the speaker model to the GPU")
    import applog
    applog.get_logger().info(f"diarization: running {model} on {device}")
    import soundfile as sf
    import torch
    waveform, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(waveform.T)  # (frames, channels) -> (channels, frames)
    hints = {"num_speakers": num_speakers}
    if min_speakers is not None:
        hints["min_speakers"] = min_speakers
    if max_speakers is not None:
        hints["max_speakers"] = max_speakers
    audio = {"waveform": waveform, "sample_rate": sample_rate}
    _say(0.08, "Detecting speakers...")
    oom_reason = None
    try:
        result = _run_pipeline(pipeline, audio, hints, on_progress)
    except Exception as exc:
        if device != "cuda" or not is_cuda_oom(exc):
            raise
        from translate_engines import redact_secrets
        oom_reason = redact_secrets(f"{type(exc).__name__}: {exc}")[:200]
    if oom_reason is not None:
        # Retried outside the except block so the failed run's traceback no
        # longer pins the GPU pipeline in memory.
        pipeline = None
        _free_gpu_memory()
        applog.get_logger().error(f"diarization: {OOM_FALLBACK_MESSAGE} ({oom_reason})")
        if run_info is not None:
            run_info.update(device="cpu", fell_back_to_cpu=True, fallback_kind="oom",
                            fallback_reason=oom_reason)
        _say(0.08, OOM_FALLBACK_MESSAGE)

        def cpu_progress(frac, message):
            _say(frac, f"Running on CPU (out of GPU memory, slower). {message}")
        try:
            pipeline, model = load_pipeline(hf_token)  # loads on CPU; never moved to the GPU
            result = _run_pipeline(pipeline, audio, hints, cpu_progress if on_progress else None)
        except Exception as cpu_exc:
            raise RuntimeError(
                "Speaker detection ran out of GPU memory and the retry on CPU failed too: "
                + redact_secrets(f"{type(cpu_exc).__name__}: {cpu_exc}")[:200]) from cpu_exc
    # pyannote.audio 4.x's pipeline(audio) returns a DiarizeOutput dataclass
    # (its .speaker_diarization attribute holds the actual Annotation)
    # instead of an Annotation directly, so .itertracks() would otherwise
    # break on 4.x with an AttributeError. 3.x's plain Annotation has no
    # such attribute, so this falls through to using it directly.
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({"start": turn.start, "end": turn.end, "speaker": speaker})

    if not return_embeddings:
        return (segments, model) if return_model else segments
    embeddings = extract_speaker_embeddings(result, annotation)
    return (segments, model, embeddings) if return_model else (segments, embeddings)


def diarize_subprocess_worker(audio_path: str, hf_token: str, num_speakers, *rest):
    """Step 4d: entry point for running diarize() in its own OS process,
    via background_jobs.start_process_job() -- pyannote's pipeline(...)
    call is one opaque call with no cooperative-cancellation checkpoint
    of its own (unlike every other job type in this app, which checks
    is_cancel_requested() between discrete units of work), so a genuine
    stop needs a real OS process to terminate() rather than a thread.

    Must stay a plain, top-level, picklable function (multiprocessing
    has to pickle the target to hand it to the child process) and must
    only ever put plain-Python, already-JSON-safe values onto
    result_queue -- segments/model/embeddings are exactly what diarize()
    already returns, never a torch tensor or pyannote object, which
    couldn't cross the process boundary at all.

    Called as (audio_path, hf_token, num_speakers, result_queue) -- the
    original shape, still used by the frozen Streamlit tab -- or as
    (audio_path, hf_token, num_speakers, options, result_queue), where
    options is a plain dict with any of use_gpu/min_speakers/max_speakers
    (Steps 101/105). The result also carries "device", the device the
    pipeline actually ran on.
    """
    result_queue = rest[-1]
    options = rest[0] if len(rest) > 1 and isinstance(rest[0], dict) else {}
    try:
        import background_jobs
        run_info = {}
        segments, model, embeddings = diarize(
            audio_path, hf_token, num_speakers=num_speakers,
            return_model=True, return_embeddings=True,
            use_gpu=bool(options.get("use_gpu")), min_speakers=options.get("min_speakers"),
            max_speakers=options.get("max_speakers"), run_info=run_info,
            on_progress=lambda frac, message: background_jobs.report_progress(
                result_queue, frac, message))
        result_queue.put(("ok", {"segments": segments, "model": model, "embeddings": embeddings,
                                 "device": run_info.get("device", "cpu"),
                                 "fell_back_to_cpu": bool(run_info.get("fell_back_to_cpu")),
                                 "fallback_reason": run_info.get("fallback_reason"),
                                 "fallback_kind": run_info.get("fallback_kind")}))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


def extract_speaker_embeddings(result, annotation) -> dict:
    """Step 8: {speaker_label: [float, ...]} one voice fingerprint per
    detected speaker, from pyannote.audio 4.x's DiarizeOutput.speaker_embeddings
    -- {} on pyannote 3.x (no such attribute there) or if extraction fails
    for any reason, since this is a bonus signal for voice-match
    suggestions, never something a diarization run itself should fail
    over just because embeddings couldn't be read out.

    NOT verified against a real pyannote 4 install -- this sandbox has no
    network (see this module's own top-of-file docstring), so this is
    written directly against pyannote's documented DiarizeOutput shape:
    speaker_embeddings is one row per speaker, in the same order
    annotation.labels() returns them in. Confirm this against a real run
    before relying on it.
    """
    raw = getattr(result, "speaker_embeddings", None)
    if raw is None:
        return {}
    try:
        labels = annotation.labels()
        return {label: [float(x) for x in raw[i]] for i, label in enumerate(labels)}
    except Exception:
        return {}


def assign_speaker_to_line(line_start: float, line_end: float, speaker_segments):
    """Pick the speaker whose segment overlaps most with this line's time range."""
    best_speaker, best_overlap = None, 0.0
    for seg in speaker_segments:
        overlap = min(line_end, seg["end"]) - max(line_start, seg["start"])
        if overlap > best_overlap:
            best_overlap, best_speaker = overlap, seg["speaker"]
    return best_speaker


def merge_speakers(lines, turns, overwrite_manual: bool = False) -> dict:
    """Relabels existing lines from diarization turns, in place, without
    touching their text or timing (no ASR involved). A line whose speaker
    was corrected by hand is left alone unless overwrite_manual is True --
    re-running detection must never silently undo a correction. Returns
    {"changed": n, "kept_manual": n}."""
    changed = kept = 0
    for ln in lines:
        new = assign_speaker_to_line(ln.start, ln.end, turns)
        if getattr(ln, "speaker_manual", False) and not overwrite_manual:
            if new != ln.speaker:
                kept += 1
            continue
        if new != ln.speaker:
            changed += 1
        ln.speaker = new
        ln.speaker_manual = False
    return {"changed": changed, "kept_manual": kept}


def save_turns(drama_dir: str, turns, num_speakers: int = None, model: str = "",
              embeddings: dict = None, min_speakers: int = None, max_speakers: int = None,
              device: str = None) -> str:
    """Stores pyannote's output next to the drama, so speakers can be
    re-merged (or voice clips extracted) later without re-running it --
    it used to live only in st.session_state and vanish on a refresh.
    Replaced on each detection run; it's the current result, not history.

    embeddings: Step 8's optional {speaker_label: [float, ...]} voice
    fingerprints (extract_speaker_embeddings()), saved alongside the
    turns -- {} (not None) when there's nothing to save, so load_embeddings
    always gets a dict back, never needing a None check of its own."""
    path = os.path.join(drama_dir, TURNS_FILE)
    os.makedirs(drama_dir, exist_ok=True)
    from core import atomic_write
    atomic_write(path, json.dumps(
        {"created_at": datetime.datetime.utcnow().isoformat(), "model": model,
         "num_speakers": num_speakers, "min_speakers": min_speakers,
         "max_speakers": max_speakers, "device": device, "turns": list(turns),
         "embeddings": embeddings or {}}, indent=2))
    return path


def _read_turns_file(drama_dir: str) -> dict:
    """The parsed turns file, or {} if it is missing or corrupt."""
    path = os.path.join(drama_dir, TURNS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_turns(drama_dir: str):
    """The stored turns list, or None if detection hasn't run for this drama."""
    return _read_turns_file(drama_dir).get("turns")


def load_last_speaker_count(drama_dir: str):
    """The `num_speakers` used for the last real detection run on this
    drama, or None if detection hasn't run yet (or that run used
    auto-detect, which is also stored as None) -- lets the UI default
    "Expected number of speakers" to whatever was actually used last
    time instead of always resetting to 0."""
    return _read_turns_file(drama_dir).get("num_speakers")


def load_last_run_info(drama_dir: str) -> dict:
    """{"min_speakers", "max_speakers", "device"} from the last detection
    run (Steps 101/105), each None if unset, no run yet, or an older file."""
    data = _read_turns_file(drama_dir)
    return {k: data.get(k) for k in ("min_speakers", "max_speakers", "device")}


def load_embeddings(drama_dir: str) -> dict:
    """The stored {speaker_label: [float, ...]} voice fingerprints from
    the last detection run, or {} if there are none (no run yet, an
    older save from before Step 8, or pyannote 3.x with nothing to save)."""
    return _read_turns_file(drama_dir).get("embeddings") or {}


def fallback_done_message(kind) -> str:
    """The past-tense sentence for a finished run that fell back to CPU."""
    return PLACEMENT_FALLBACK_DONE_MESSAGE if kind == "placement" else OOM_FALLBACK_DONE_MESSAGE
