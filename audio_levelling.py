"""Per-clip loudness levelling for generated speech, applied when a narration track is assembled."""

import numpy as np

FRAME_MS = 50
TARGET_DBFS = -18.0
# A quiet voice is lifted by at most this much and a loud one lowered by at most this much,
# so a clip that is quiet on purpose (a whisper) is not turned into a shout.
MAX_BOOST_DB = 9.0
MAX_CUT_DB = 12.0
PEAK_CEILING_DBFS = -1.0
# Frames this far below the loudest frame, or below the absolute gate, are pauses and
# breaths; counting them would make every clip with long gaps look quieter than it speaks.
ACTIVE_RANGE_DB = 20.0
ACTIVE_GATE_DBFS = -50.0
# A clip whose speech is barely above hiss is left alone: lifting it would only lift the hiss.
MIN_SPEECH_DBFS = -45.0
# Lifting a clip must not bring its own background above this level.
NOISE_CEILING_DBFS = -50.0
DIGITAL_SILENCE_DBFS = -90.0
MIN_STEP_DB = 0.5


def _frame_levels(samples: np.ndarray, rate: int, full_scale: float):
    """RMS level of each frame in dBFS, plus the peak level of the whole clip."""
    size = max(1, int(rate * FRAME_MS / 1000))
    usable = len(samples) // size * size
    if usable == 0:
        return np.array([]), -np.inf
    frames = samples[:usable].astype(np.float64).reshape(-1, size) / full_scale
    rms = np.sqrt((frames ** 2).mean(axis=1))
    with np.errstate(divide="ignore"):
        levels = 20 * np.log10(np.maximum(rms, 1e-12))
    peak = 20 * np.log10(max(float(np.abs(samples).max()) / full_scale, 1e-12))
    return levels, peak


def speech_level_dbfs(samples, rate: int, full_scale: float):
    """Loudness of the speech in a clip in dBFS (power mean of the active frames), or None
    when no frame is loud enough to be speech."""
    levels, _ = _frame_levels(np.asarray(samples), rate, full_scale)
    if levels.size == 0:
        return None
    active = levels[(levels > ACTIVE_GATE_DBFS) & (levels > levels.max() - ACTIVE_RANGE_DB)]
    if active.size == 0:
        return None
    return float(10 * np.log10(np.mean(10 ** (active / 10))))


def gain_for(samples, rate: int, full_scale: float) -> float:
    """dB to apply so the clip's speech sits at TARGET_DBFS, 0.0 when it should be left as is."""
    samples = np.asarray(samples)
    levels, peak = _frame_levels(samples, rate, full_scale)
    speech = speech_level_dbfs(samples, rate, full_scale)
    if speech is None or speech < MIN_SPEECH_DBFS:
        return 0.0
    gain = min(MAX_BOOST_DB, max(-MAX_CUT_DB, TARGET_DBFS - speech))
    if gain > 0:
        gain = min(gain, max(0.0, PEAK_CEILING_DBFS - peak))
        # Pauses are the only place a clip's own hiss can be measured; a clip with none
        # gives no evidence of hiss.
        pauses = levels[(levels > DIGITAL_SILENCE_DBFS) & (levels <= levels.max() - ACTIVE_RANGE_DB)]
        if pauses.size:
            gain = min(gain, max(0.0, NOISE_CEILING_DBFS - float(np.percentile(pauses, 10))))
    return gain if abs(gain) >= MIN_STEP_DB else 0.0


def _clip_arguments(clip):
    samples = np.array(clip.get_array_of_samples())
    if clip.channels > 1:
        samples = samples.reshape(-1, clip.channels).mean(axis=1)
    return samples, clip.frame_rate, float(1 << (8 * clip.sample_width - 1))


def clip_speech_level(clip):
    """speech_level_dbfs for a pydub AudioSegment."""
    return speech_level_dbfs(*_clip_arguments(clip))


def level_clip(clip):
    """A pydub AudioSegment brought to the common speech level (the same clip when no change is due)."""
    gain = gain_for(*_clip_arguments(clip))
    return clip.apply_gain(gain) if gain else clip
