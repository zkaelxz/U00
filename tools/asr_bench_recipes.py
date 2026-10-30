"""Recipes for tools.asr_bench: the grammar, the backend registry and the runners.

A recipe is one way to hear the same stretch of audio. Every runner calls the
app's own transcription code (core.transcribe_for_timing, asr_backend,
forced_align) with the recipe's options, so the bench measures what the app
would do and reimplements no ASR.

A new recipe backend (an `anime_whisper`, say) is one BackendSpec in BACKENDS
below: the keys it honours, what it loads, and a runner returning
{"start", "end", "text"} segments with times relative to the window.
"""
import contextlib
import math
import os
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

import asr_backend
import audio_preprocess
import whisper_transcribe
import forced_align
import memory_headroom
import qwen3_native
import sensitivity_preset as presets
import whisper_models
from services import asr_options_service, timing_settings_service, transcribe_service, transcribe_options_service, transcribe_speed_service

MAX_BEAM = 20
DETECTORS = ("standard", "asmr")
PRESET_KEYS = ("beam", "vad", "prompt", "align", "detector", "difficult")

GRAMMAR = """\
recipe grammar:  BACKEND[:MODEL][,KEY=VALUE]...

  BACKEND  whisper | qwen3_asr | qwen3_asr_vad | qwen3_asr_long | anime_whisper
  MODEL    a Whisper model key from the Transcribe stage (small, medium, large-v3,
           large-v3-turbo, ja-1.5b, kotoba-v2). Only whisper and qwen3_asr take
           one; omitted, the title's saved model is used. A model that is not
           recommended for the title's language is refused.
  KEYs     (each at most once; a key a backend does not use is refused)
    vad=normal|sensitive    speech detector preset of Whisper's own detector
                            (default: the title's saved preset)       whisper, qwen3_asr
    beam=1..20              beam size (default: the title's saved value)  whisper, qwen3_asr
    prompt=on|off           the automatic name/glossary initial prompt (default on)
                                                                       whisper, qwen3_asr
    align=on|off            refine line times with the Qwen3 forced aligner (default off)
                                                          whisper, qwen3_asr_vad
                            (qwen3_asr_long always aligns)
    detector=standard|asmr  voice detector (default: the Settings choice, auto)
                                                          qwen3_asr_vad, qwen3_asr_long,
                                                          anime_whisper
    difficult=on|off        the title's "Difficult audio" option: levels the audio, uses the
                            sensitive detector (an explicit vad= wins) and relaxes Whisper's
                            skip rule (default: the title's saved choice)  whisper, qwen3_asr

anime_whisper is Japanese only and for personal, non-commercial use; it downloads its
own pinned model on first use and takes no MODEL.

examples:
  whisper:large-v3-turbo
  whisper:kotoba-v2,align=on
  whisper:large-v3-turbo,vad=sensitive,beam=1
  whisper:large-v3,difficult=on
  qwen3_asr_vad,detector=asmr
  qwen3_asr_long
  anime_whisper

The first --config is the baseline. Anything not named in a recipe is what the
title already uses, so a recipe only has to say what differs."""


class RecipeError(ValueError):
    """A recipe string the grammar or this title refuses."""


@dataclass(frozen=True)
class Recipe:
    backend: str
    model: Optional[str] = None
    vad: Optional[str] = None
    beam: Optional[int] = None
    prompt: bool = True
    align: bool = False
    detector: Optional[str] = None
    difficult: Optional[bool] = None
    label: str = ""


@dataclass
class RunContext:
    """What one recipe run needs; nothing in it is written back to the title."""
    title_id: int
    drama: dict
    language: str
    wav: str
    use_gpu: bool
    local_model_path: Optional[str]
    notes: list = field(default_factory=list)

    def note_gpu_fallback(self, exc) -> None:
        self.notes.append("GPU unavailable, ran on CPU: " + whisper_models.short_reason(exc))


@dataclass(frozen=True)
class BackendSpec:
    name: str
    keys: frozenset
    takes_model: bool
    run: Callable
    loads: Callable
    # (GPU, CPU) seconds of work per second of audio when no run has been measured.
    rtf: tuple


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _difficult(ctx: RunContext, r: Recipe) -> bool:
    return bool(ctx.drama.get("difficult_audio") if r.difficult is None else r.difficult)


@contextlib.contextmanager
def _levelled_wav(ctx: RunContext, r: Recipe):
    """The clip to transcribe: a levelled temporary copy for a Difficult audio recipe
    (the same ffmpeg step the app runs), else the clip itself. Without ffmpeg the clip
    is used and the report says so."""
    path, out = ctx.wav, os.path.join(os.path.dirname(os.path.abspath(ctx.wav)), "leveled.wav")
    if _difficult(ctx, r):
        try:
            path = audio_preprocess.level_audio(ctx.wav, out)
        except audio_preprocess.AudioLevelingUnavailable as exc:
            ctx.notes.append(str(exc))
    try:
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.remove(out)


def _whisper_pass(ctx: RunContext, r: Recipe, wav: str = None) -> list:
    drama = ctx.drama
    difficult = _difficult(ctx, r)
    row = presets.difficult_audio_view({**drama, "difficult_audio": difficult})
    preset = presets.normalize(r.vad or row.get("sensitivity_preset"))
    # Only a Difficult audio recipe passes the skip-rule numbers, so every other recipe
    # calls the app's code exactly as before.
    extra = {}
    if difficult:
        values = timing_settings_service.values()
        extra = {"difficult_audio": True,
                 "no_speech_threshold": values["difficult_no_speech_threshold"],
                 "log_prob_threshold": values["difficult_log_prob_threshold"]}
    prompt = ""
    if r.prompt:
        prompt = transcribe_service.build_auto_initial_prompt(ctx.title_id)
    segments = whisper_transcribe.transcribe_for_timing(
        wav or ctx.wav, r.model, language=ctx.language, use_gpu=ctx.use_gpu,
        local_model_path=ctx.local_model_path, hf_token=None, initial_prompt=prompt,
        beam_size=r.beam or drama.get("beam_size") or transcribe_options_service._DEFAULT_TUNING["beam_size"],
        min_silence_duration_ms=(drama.get("min_silence_ms")
                                 or transcribe_options_service._DEFAULT_TUNING["min_silence_ms"]),
        vad_threshold=presets.effective_vad_threshold(drama.get("vad_threshold"), preset),
        on_gpu_fallback=ctx.note_gpu_fallback,
        fast_mode=bool(drama.get("whisper_fast_mode")),
        hallucination_silence_sec=transcribe_options_service.stored_hallucination_silence_sec(drama),
        repeat_guard=bool(drama.get("whisper_repeat_guard")), sensitivity_preset=preset, **extra)
    info = whisper_models.get_whisper_device_info(r.model, ctx.use_gpu, ctx.local_model_path)
    if info.get("gpu_error"):
        ctx.notes.append("GPU unavailable, ran on CPU: " + info["gpu_error"])
    return segments


def run_whisper(ctx: RunContext, r: Recipe) -> list:
    with _levelled_wav(ctx, r) as wav:
        segments = _whisper_pass(ctx, r, wav)
        if r.align and segments:
            # The aligner's load is judged against free memory, and Whisper has finished.
            whisper_models.release_gpu_models()
            segments = forced_align.refine_whisper_segments(
                wav, segments, ctx.language, use_gpu=ctx.use_gpu,
                on_gpu_fallback=lambda task, exc: ctx.note_gpu_fallback(exc))
    return segments


def run_qwen3_asr(ctx: RunContext, r: Recipe) -> list:
    with _levelled_wav(ctx, r) as wav:
        segments = _whisper_pass(ctx, r, wav)
        if not segments:
            return []
        whisper_models.release_gpu_models()
        return asr_backend.get_backend("qwen3_asr").transcribe(
            wav, ctx.language, segments, use_gpu=ctx.use_gpu,
            batch_size=asr_options_service.get_qwen_asr_batch_size(),
            on_gpu_fallback=lambda task, exc: ctx.note_gpu_fallback(exc))


def run_qwen3_vad(ctx: RunContext, r: Recipe) -> list:
    detector = r.detector or asr_options_service.resolve_voice_detector(ctx.drama, ctx.language)
    return asr_backend.get_backend(r.backend).transcribe(
        ctx.wav, ctx.language, use_gpu=ctx.use_gpu,
        batch_size=asr_options_service.get_qwen_asr_batch_size(), refine_timing=r.align,
        detector=detector, on_notice=ctx.notes.append,
        on_gpu_fallback=lambda task, exc: ctx.note_gpu_fallback(exc))


def run_anime_whisper(ctx: RunContext, r: Recipe) -> list:
    detector = r.detector or asr_options_service.resolve_voice_detector(ctx.drama, ctx.language)
    return asr_backend.get_backend("anime_whisper").transcribe(
        ctx.wav, ctx.language, use_gpu=ctx.use_gpu, detector=detector,
        on_notice=ctx.notes.append,
        on_gpu_fallback=lambda task, exc: ctx.note_gpu_fallback(exc))


# What each recipe loads: (headroom kind, headroom name, label).
def _whisper_load(r):
    return [("whisper", r.model, f"Whisper {r.model}")]


def _aligner_load():
    return [("aligner", "qwen3", "Qwen3 forced aligner")]


def _qwen_load():
    return [("qwen_asr", asr_backend.Qwen3ASRBackend().model_size,
             f"Qwen3-ASR {asr_backend.Qwen3ASRBackend().model_size}")]


BACKENDS = {
    "whisper": BackendSpec(
        "whisper", frozenset({"vad", "beam", "prompt", "align", "difficult"}), True, run_whisper,
        lambda r: _whisper_load(r) + (_aligner_load() if r.align else []), (0.12, 1.0)),
    "qwen3_asr": BackendSpec(
        "qwen3_asr", frozenset({"vad", "beam", "prompt", "difficult"}), True, run_qwen3_asr,
        lambda r: _whisper_load(r) + _qwen_load(), (0.3, 2.0)),
    "qwen3_asr_vad": BackendSpec(
        "qwen3_asr_vad", frozenset({"align", "detector"}), False, run_qwen3_vad,
        lambda r: _qwen_load() + (_aligner_load() if r.align else []), (0.25, 1.5)),
    "qwen3_asr_long": BackendSpec(
        "qwen3_asr_long", frozenset({"detector"}), False, run_qwen3_vad,
        lambda r: _qwen_load() + _aligner_load(), (0.35, 2.0)),
    "anime_whisper": BackendSpec(
        "anime_whisper", frozenset({"detector"}), False, run_anime_whisper,
        lambda r: [("whisper", "anime-whisper", "anime-whisper")], (0.15, 1.0)),
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _on_off(key: str, value: str) -> bool:
    if value not in ("on", "off"):
        raise RecipeError(f"{key}= must be on or off, not '{value}'.")
    return value == "on"


def _choice(key: str, value: str, choices) -> str:
    if value not in choices:
        raise RecipeError(f"{key}= must be one of {', '.join(choices)}, not '{value}'.")
    return value


def parse_recipe(text: str, language: str, default_model: str) -> Recipe:
    """The Recipe for `text`, or RecipeError saying what is wrong. language and
    default_model are the title's, so a model that does not fit them is refused here."""
    head, *pairs = [part.strip() for part in text.strip().split(",")]
    backend, _, model = head.partition(":")
    backend, model = backend.strip(), model.strip()
    spec = BACKENDS.get(backend)
    if spec is None:
        raise RecipeError(f"'{backend}' is not a recipe backend (choices: {', '.join(BACKENDS)}).")
    if model and not spec.takes_model:
        raise RecipeError(f"{backend} does not use a Whisper model; write '{backend}' without ':{model}'.")
    if spec.takes_model:
        model = model or default_model
        if model not in whisper_models.WHISPER_MODELS:
            raise RecipeError(f"'{model}' is not a Whisper model (choices: "
                              f"{', '.join(whisper_models.WHISPER_MODELS)}).")
        problem = whisper_models.whisper_model_language_problem(model, language)
        if problem:
            raise RecipeError(f"{model} for a {language} title: {problem}")
    values = {}
    for pair in pairs:
        key, eq, value = pair.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if not eq or not value:
            raise RecipeError(f"'{pair}' is not KEY=VALUE.")
        if key not in PRESET_KEYS:
            raise RecipeError(f"'{key}' is not a recipe key (keys: {', '.join(PRESET_KEYS)}).")
        if key in values:
            raise RecipeError(f"'{key}' is given twice.")
        if key not in spec.keys:
            raise RecipeError(f"{backend} does not use {key}= (it takes: "
                              f"{', '.join(sorted(spec.keys)) or 'no keys'}).")
        values[key] = value
    fields = {}
    if "vad" in values:
        fields["vad"] = _choice("vad", values["vad"], presets.PRESETS)
    if "beam" in values:
        try:
            beam = int(values["beam"])
        except ValueError:
            beam = 0
        if not 1 <= beam <= MAX_BEAM:
            raise RecipeError(f"beam= must be a whole number from 1 to {MAX_BEAM}.")
        fields["beam"] = beam
    if "prompt" in values:
        fields["prompt"] = _on_off("prompt", values["prompt"])
    if "align" in values:
        fields["align"] = _on_off("align", values["align"])
    if "detector" in values:
        fields["detector"] = _choice("detector", values["detector"], DETECTORS)
    if "difficult" in values:
        fields["difficult"] = _on_off("difficult", values["difficult"])
    recipe = Recipe(backend, model or None, **fields)
    return replace(recipe, label=canonical_label(recipe))


def canonical_label(r: Recipe) -> str:
    parts = [r.backend + (f":{r.model}" if r.model else "")]
    if r.vad:
        parts.append(f"vad={r.vad}")
    if r.beam:
        parts.append(f"beam={r.beam}")
    if not r.prompt:
        parts.append("prompt=off")
    if r.align:
        parts.append("align=on")
    if r.detector:
        parts.append(f"detector={r.detector}")
    if r.difficult is not None:
        parts.append(f"difficult={'on' if r.difficult else 'off'}")
    return ",".join(parts)


def unique_labels(recipes: list) -> list:
    """The same recipe passed twice (a noise check) stays two columns."""
    seen, out = {}, []
    for r in recipes:
        seen[r.label] = seen.get(r.label, 0) + 1
        label = r.label if seen[r.label] == 1 else f"{r.label} #{seen[r.label]}"
        out.append(replace(r, label=label))
    return out


# ---------------------------------------------------------------------------
# Dry-run facts
# ---------------------------------------------------------------------------

def availability_problem(recipe: Recipe, language: str) -> Optional[str]:
    """Why this recipe cannot run on this PC (a package missing, a language the
    backend does not cover), or None. The same checks Compare transcription makes."""
    from services import compare_transcription_service
    problem = compare_transcription_service._backend_problem(recipe.backend, language)
    if problem:
        return problem
    if recipe.align or recipe.backend == "qwen3_asr_long":
        return (qwen3_native.aligner_language_problem(language)
                or qwen3_native.transformers_problem("Qwen3 forced alignment"))
    return None


def describe_loads(recipe: Recipe) -> str:
    return " + ".join(label for _, _, label in BACKENDS[recipe.backend].loads(recipe))


def peak_vram_estimate_mb(recipe: Recipe, use_gpu: bool) -> Optional[float]:
    """The largest single model the recipe loads (they are released between stages),
    or None when one is not in the headroom table."""
    sizes = [memory_headroom.estimate_mb(kind, name) for kind, name, _ in
             BACKENDS[recipe.backend].loads(recipe)]
    if not sizes or any(s is None for s in sizes):
        return None
    return float(max(s[0] if use_gpu else s[1] for s in sizes))


LOAD_SECONDS = 20.0


def estimate_seconds(recipe: Recipe, window_s: float, use_gpu: bool) -> float:
    """Rough: model loads plus window length times a speed. Whisper uses the speed
    the app measured on this PC when it has one; everything else uses a guess."""
    spec = BACKENDS[recipe.backend]
    speed = None
    if recipe.model:
        try:
            speed = transcribe_speed_service.measured_transcribe_speed(recipe.model, use_gpu)
        except Exception:
            speed = None
    factor = spec.rtf[0 if use_gpu else 1]
    if recipe.backend == "whisper" and speed and speed > 0:
        factor = 1.0 / speed
    if recipe.align and recipe.backend == "whisper":
        factor *= 1.4
    return len(spec.loads(recipe)) * LOAD_SECONDS + window_s * factor


def percentile(values: list, fraction: float) -> Optional[float]:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]
