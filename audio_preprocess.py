"""
audio_preprocess.py -- optional audio preprocessing before transcription:
vocal separation (stripping background music) via Demucs.

WHY THIS EXISTS: Whisper's accuracy on audio dramas/livestreams with a
music bed under the dialogue is meaningfully worse than on clean speech
-- a real, independently-confirmed gap (this app had no preprocessing
step at all before this, and a comparable project turned to an
OBS-level, GPU-vendor-specific noise filter for exactly this reason,
because nothing at the Python/pipeline level existed to reach for). A
generic noise-reduction filter (spectral gating, hiss/static removal)
doesn't actually solve this -- background music isn't noise in that
sense, it's a second, structured audio source mixed into the same
track. Demucs is a real source-separation model, trained specifically
to split a mixed track into vocals/drums/bass/other stems; keeping only
the vocals stem is what actually gets a music bed out of Whisper's way.

Deliberately opt-in, not run automatically on every transcription:
Demucs is a full neural network pass over the whole file (real time
added, roughly comparable to Whisper's own pass), and it downloads its
own model (~80MB for the default htdemucs) on first use. Clean
dialogue-only audio with no music bed gets no benefit from running it
-- there's nothing to separate out, so it would only cost time.

Requires: `pip install demucs` (pulls in torch/torchaudio, already a
transitive dependency of faster-whisper elsewhere in this app).

Step 6 added a second, preferred backend: `audio-separator` (MIT,
https://github.com/nomadkaraoke/python-audio-separator) running a Mel-Band
RoFormer vocal model, which gives cleaner vocals than Demucs on content
with a music bed. Demucs's own repo is archived and no longer maintained,
so it stays only as the fallback. Requires `pip install audio-separator`.
"""
import os
import shutil
import tempfile

SEPARATION_BACKENDS = {
    "auto": "Auto -- Mel-Band RoFormer if installed, otherwise Demucs",
    "audio_separator": "Mel-Band RoFormer (audio-separator) -- cleanest vocals",
    "demucs": "Demucs -- older fallback",
}
# Kimberley Jensen's Mel-Band RoFormer vocal model, as named in
# audio-separator's own model registry (models.json).
MEL_ROFORMER_VOCAL_MODEL = "vocals_mel_band_roformer.ckpt"
# BAIHE_AUDIO_SEP_MODEL_DIR: set by portable.py's activate_portable_mode()
# under Step 10's portable mode, so a copied app folder's downloaded
# separator model comes with it -- audio-separator itself has no env var
# of its own for this, unlike huggingface_hub's HF_HOME.
_MODEL_DIR = os.environ.get("BAIHE_AUDIO_SEP_MODEL_DIR") or os.path.join(
    os.path.expanduser("~"), ".cache", "audio-separator-models")

# Step 4g: neither backend exposes a per-chunk callback of its own (each
# is one opaque call over the whole file), so real progress and a
# genuine mid-run cancel are done outside the backend -- split the input
# into overlapping windows, run each one through the backend separately,
# and recombine with a short linear crossfade at each boundary so the
# seam isn't audible.
DEFAULT_CHUNK_SECONDS = 30.0
DEFAULT_CHUNK_OVERLAP_SECONDS = 1.0


class VocalSeparationError(RuntimeError):
    """Raised when a separation backend isn't installed, or separation
    itself fails, so callers can show a clear message instead of a raw
    import error or an unrelated-looking traceback from deep inside it."""


class VocalSeparationCancelled(VocalSeparationError):
    """Raised by _separate_vocals_chunked when cancel_check_cb() returns
    True before a chunk starts -- a distinct type from a real failure,
    so a caller can report a clean "cancelled" outcome instead of
    "vocal separation failed", and so separate_vocals() below never
    treats a cancel as a reason to try the next backend."""


def separate_vocals(audio_path: str, out_path: str, backend: str = "auto",
                    progress_cb=None, cancel_check_cb=None) -> str:
    """Writes just the vocals of audio_path to out_path and returns
    out_path. backend: "auto" tries Mel-Band RoFormer (audio-separator)
    first and falls back to Demucs if it's missing or fails; naming a
    backend uses only that one.

    progress_cb, if given, is called with a 0..1 fraction as separation
    proceeds chunk by chunk. cancel_check_cb, if given, is checked
    before each chunk starts; a True result raises VocalSeparationCancelled
    (never treated as a failure worth falling back to the other backend
    for)."""
    order = {"auto": ("audio_separator", "demucs")}.get(backend, (backend,))
    errors = []
    for name in order:
        try:
            return _BACKENDS[name](audio_path, out_path, progress_cb=progress_cb,
                                   cancel_check_cb=cancel_check_cb)
        except VocalSeparationCancelled:
            raise
        except VocalSeparationError as exc:
            errors.append(str(exc))
    raise VocalSeparationError("\n".join(errors))


def separate_vocals_audio_separator(audio_path: str, out_path: str,
                                     model: str = MEL_ROFORMER_VOCAL_MODEL,
                                     progress_cb=None, cancel_check_cb=None) -> str:
    """Mel-Band RoFormer via audio-separator. Downloads the model once
    (to ~/.cache/audio-separator-models) on first use. The model is
    loaded once and reused across every chunk, not reloaded per chunk."""
    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise VocalSeparationError(
            "Mel-Band RoFormer vocal separation needs: pip install audio-separator") from exc

    work_dir = tempfile.mkdtemp(prefix="baihe_separator_", dir=os.path.dirname(out_path) or None)
    try:
        try:
            separator = Separator(output_dir=work_dir, model_file_dir=_MODEL_DIR,
                                  output_single_stem="Vocals")
            separator.load_model(model_filename=model)
        except Exception as exc:
            raise VocalSeparationError(
                f"audio-separator failed to load model '{model}': {exc}") from exc

        def _process_chunk(chunk_path, chunk_out_path):
            try:
                outputs = separator.separate(chunk_path)
            except Exception as exc:
                raise VocalSeparationError(
                    f"audio-separator failed to separate '{chunk_path}': {exc}") from exc
            vocals = [f for f in outputs if "vocal" in os.path.basename(f).lower()] or list(outputs)
            if not vocals:
                raise VocalSeparationError(f"audio-separator model '{model}' wrote no vocals stem.")
            # Newer audio-separator returns full paths, older ones names in output_dir.
            produced = vocals[0] if os.path.isabs(vocals[0]) else os.path.join(work_dir, vocals[0])
            shutil.move(produced, chunk_out_path)

        _separate_vocals_chunked(audio_path, out_path, _process_chunk,
                                 progress_cb=progress_cb, cancel_check_cb=cancel_check_cb)
        return out_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def separate_vocals_demucs(audio_path: str, out_path: str, model: str = "htdemucs",
                           progress_cb=None, cancel_check_cb=None) -> str:
    """
    Runs Demucs source separation on audio_path and writes just its
    vocals stem to out_path -- everything else Demucs identifies
    (background music, incidental sound effects) is discarded. Returns
    out_path, for chaining directly into whatever reads audio next
    (transcribe_for_timing expects exactly this: a path to an audio file).

    model: which Demucs model to use. "htdemucs" (the default, a hybrid
    transformer architecture) is Demucs' own recommended general-purpose
    choice; "htdemucs_ft" is a fine-tuned variant that's noticeably
    slower (it runs multiple passes internally) but sometimes cleaner --
    worth trying if htdemucs's separation still leaves audible music
    bleeding through on a particular source.

    The model is loaded once (via Separator(model=model)) and reused
    across every chunk, not reloaded per chunk.
    """
    try:
        from demucs.api import Separator, save_audio
    except ImportError as exc:
        raise VocalSeparationError(
            "Vocal separation needs Demucs: pip install demucs") from exc

    separator = Separator(model=model)

    def _process_chunk(chunk_path, chunk_out_path):
        try:
            _origin, separated = separator.separate_audio_file(chunk_path)
        except Exception as exc:
            raise VocalSeparationError(
                f"Demucs failed to separate '{chunk_path}': {exc}") from exc
        if "vocals" not in separated:
            raise VocalSeparationError(
                f"Demucs model '{model}' didn't produce a vocals stem (got: "
                f"{', '.join(separated.keys())} instead) -- pick a different model.")
        save_audio(separated["vocals"], chunk_out_path, samplerate=separator.samplerate)

    _separate_vocals_chunked(audio_path, out_path, _process_chunk,
                             progress_cb=progress_cb, cancel_check_cb=cancel_check_cb)
    return out_path


def _separate_vocals_chunked(audio_path: str, out_path: str, process_chunk_fn,
                             chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
                             overlap_seconds: float = DEFAULT_CHUNK_OVERLAP_SECONDS,
                             progress_cb=None, cancel_check_cb=None):
    """Splits audio_path into overlapping windows, runs process_chunk_fn
    (a backend-specific "separate this one chunk file" call) on each in
    turn, and recombines the results into out_path with a short linear
    crossfade at each boundary.

    process_chunk_fn(chunk_in_path, chunk_out_path) must write that
    chunk's separated vocals to chunk_out_path; its own exceptions (a
    real separation failure) propagate up unchanged.

    A short file (<= chunk_seconds) still goes through this same path as
    a single chunk, so progress/cancel behave uniformly regardless of
    length rather than needing a separate no-chunking branch.

    Uses soundfile (not torchaudio) for the plain-WAV reads/writes here,
    same reasoning as Step 4c: no compiled-per-FFmpeg-version DLLs.
    """
    import soundfile as sf

    info = sf.info(audio_path)
    total_samples, in_sr = info.frames, info.samplerate
    window = max(1, int(chunk_seconds * in_sr))
    overlap = max(0, min(int(overlap_seconds * in_sr), window - 1))
    step = window - overlap

    starts = [s for s in range(0, max(total_samples, 1), step) if s < total_samples] or [0]

    work_dir = tempfile.mkdtemp(prefix="baihe_vocalsep_chunks_")
    try:
        waveform, _ = sf.read(audio_path, dtype="float32", always_2d=True)
        combined, out_sr = None, None
        n_chunks = len(starts)
        for i, start in enumerate(starts):
            if cancel_check_cb and cancel_check_cb():
                raise VocalSeparationCancelled("Vocal separation was cancelled.")
            end = min(start + window, total_samples)
            chunk_in = os.path.join(work_dir, f"chunk_{i:04d}_in.wav")
            chunk_out = os.path.join(work_dir, f"chunk_{i:04d}_out.wav")
            sf.write(chunk_in, waveform[start:end], in_sr)
            process_chunk_fn(chunk_in, chunk_out)
            chunk_vocals, chunk_sr = sf.read(chunk_out, dtype="float32", always_2d=True)
            if combined is None:
                combined, out_sr = chunk_vocals, chunk_sr
            else:
                combined = _crossfade_append(combined, chunk_vocals, int(overlap_seconds * out_sr))
            if progress_cb:
                progress_cb((i + 1) / n_chunks)
        sf.write(out_path, combined, out_sr)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _crossfade_append(prev, nxt, overlap_samples: int):
    """Appends nxt onto prev, linearly crossfading the last
    overlap_samples of prev with the first overlap_samples of nxt so a
    chunk boundary isn't an audible seam. overlap_samples is clamped to
    what both arrays can actually provide (e.g. a short final chunk)."""
    import numpy as np
    overlap_samples = max(0, min(overlap_samples, len(prev), len(nxt)))
    if overlap_samples == 0:
        return np.concatenate([prev, nxt], axis=0)
    fade_out = np.linspace(1.0, 0.0, overlap_samples, dtype=np.float32).reshape(-1, 1)
    fade_in = 1.0 - fade_out
    blended = prev[-overlap_samples:] * fade_out + nxt[:overlap_samples] * fade_in
    return np.concatenate([prev[:-overlap_samples], blended, nxt[overlap_samples:]], axis=0)


_BACKENDS = {"audio_separator": separate_vocals_audio_separator, "demucs": separate_vocals_demucs}
