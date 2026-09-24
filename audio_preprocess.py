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
"""
import os


class VocalSeparationError(RuntimeError):
    """Raised when Demucs isn't installed, or separation itself fails,
    so callers can show a clear message instead of a raw import error or
    an unrelated-looking traceback from deep inside Demucs."""


def separate_vocals(audio_path: str, out_path: str, model: str = "htdemucs") -> str:
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
    """
    try:
        from demucs.api import Separator, save_audio
    except ImportError as exc:
        raise VocalSeparationError(
            "Vocal separation needs Demucs: pip install demucs") from exc

    separator = Separator(model=model)
    try:
        _origin, separated = separator.separate_audio_file(audio_path)
    except Exception as exc:
        raise VocalSeparationError(
            f"Demucs failed to separate '{audio_path}': {exc}") from exc

    if "vocals" not in separated:
        raise VocalSeparationError(
            f"Demucs model '{model}' didn't produce a vocals stem (got: "
            f"{', '.join(separated.keys())} instead) -- pick a different model.")

    save_audio(separated["vocals"], out_path, samplerate=separator.samplerate)
    return out_path
