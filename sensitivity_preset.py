"""
sensitivity_preset.py -- the per-title "normal" / "sensitive" transcription preset.

"normal" is the app's long-standing behaviour and changes nothing. "sensitive"
trades false text on music or breathing for catching quieter or faster speech:
a lower speech-detector threshold and a decode without the repeat penalties
that can suppress genuinely repeated short dialogue ("好的好的").
core.filter_hallucinated_segments still runs in both presets as the backstop
against loops.
"""

NORMAL = "normal"
SENSITIVE = "sensitive"
PRESETS = (NORMAL, SENSITIVE)

DEFAULT_VAD_THRESHOLD = 0.5
SENSITIVE_VAD_THRESHOLD = 0.35

# These two are what stop a repeated short phrase from being decoded twice.
_REPEAT_PENALTIES = ("no_repeat_ngram_size", "repetition_penalty")


def normalize(value) -> str:
    """A stored value from before the setting existed (None) or an unknown one is "normal"."""
    return value if value in PRESETS else NORMAL


def effective_vad_threshold(stored, preset) -> float:
    """The threshold a run uses. The Transcribe form sends the number back on every
    save, so a stored value equal to the default means "never changed it": only a
    different one is the owner's own and wins over the preset."""
    threshold = stored or DEFAULT_VAD_THRESHOLD
    if normalize(preset) == SENSITIVE and threshold == DEFAULT_VAD_THRESHOLD:
        return SENSITIVE_VAD_THRESHOLD
    return threshold


def stored_vad_threshold(drama) -> float:
    return effective_vad_threshold(drama.get("vad_threshold"), drama.get("sensitivity_preset"))


def decode_kwargs(preset, anti_loop_kwargs: dict, repeat_guard_kwargs: dict | None = None,
                  repeat_guard: bool = False) -> dict:
    """Whisper's anti-loop decode settings for `preset`. The title's own "Whisper repeat
    guard" toggle decides the repeat penalties when it is on; a title that never turned it
    on gets the preset's default, which is none in both presets: the guard is off by
    default and "sensitive" must not suppress genuinely repeated short dialogue. The
    preset therefore never overrides an explicit guard."""
    kwargs = dict(anti_loop_kwargs)
    if repeat_guard:
        kwargs.update(repeat_guard_kwargs or {})
    elif normalize(preset) != NORMAL:
        kwargs = {k: v for k, v in kwargs.items() if k not in _REPEAT_PENALTIES}
    return kwargs
