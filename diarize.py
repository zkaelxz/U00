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


def diarize(audio_path: str, hf_token: str, num_speakers: int = None, return_model: bool = False,
           return_embeddings: bool = False, use_gpu: bool = False,
           min_speakers: int = None, max_speakers: int = None, run_info: dict = None):
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
    fills with {"device": "cuda"|"cpu"}, the device actually used.
    """
    num_speakers, min_speakers, max_speakers = validate_speaker_hints(
        num_speakers, min_speakers, max_speakers)
    pipeline, model = load_pipeline(hf_token)
    device = _place_pipeline(pipeline, use_gpu)
    if run_info is not None:
        run_info["device"] = device
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
    result = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **hints)
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
        run_info = {}
        segments, model, embeddings = diarize(
            audio_path, hf_token, num_speakers=num_speakers,
            return_model=True, return_embeddings=True,
            use_gpu=bool(options.get("use_gpu")), min_speakers=options.get("min_speakers"),
            max_speakers=options.get("max_speakers"), run_info=run_info)
        result_queue.put(("ok", {"segments": segments, "model": model, "embeddings": embeddings,
                                 "device": run_info.get("device", "cpu")}))
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


def manual_lines_that_would_change(lines, turns) -> list:
    """Lines whose speaker was set by hand (speaker_manual) and that a
    re-merge with `turns` would relabel -- what to name in a confirmation
    before overwriting them."""
    return [ln for ln in lines if getattr(ln, "speaker_manual", False)
            and assign_speaker_to_line(ln.start, ln.end, turns) != ln.speaker]


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.datetime.utcnow().isoformat(), "model": model,
                   "num_speakers": num_speakers, "min_speakers": min_speakers,
                   "max_speakers": max_speakers, "device": device, "turns": list(turns),
                   "embeddings": embeddings or {}}, f, indent=2)
    return path


def load_turns(drama_dir: str):
    """The stored turns list, or None if detection hasn't run for this drama."""
    path = os.path.join(drama_dir, TURNS_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("turns")


def load_last_speaker_count(drama_dir: str):
    """The `num_speakers` used for the last real detection run on this
    drama, or None if detection hasn't run yet (or that run used
    auto-detect, which is also stored as None) -- lets the UI default
    "Expected number of speakers" to whatever was actually used last
    time instead of always resetting to 0."""
    path = os.path.join(drama_dir, TURNS_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("num_speakers")


def load_last_run_info(drama_dir: str) -> dict:
    """{"min_speakers", "max_speakers", "device"} from the last detection
    run (Steps 101/105), each None if unset, no run yet, or an older file."""
    path = os.path.join(drama_dir, TURNS_FILE)
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    return {k: data.get(k) for k in ("min_speakers", "max_speakers", "device")}


def load_embeddings(drama_dir: str) -> dict:
    """The stored {speaker_label: [float, ...]} voice fingerprints from
    the last detection run, or {} if there are none (no run yet, an
    older save from before Step 8, or pyannote 3.x with nothing to save)."""
    path = os.path.join(drama_dir, TURNS_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("embeddings") or {}
