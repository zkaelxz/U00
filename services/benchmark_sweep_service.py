"""
services/benchmark_sweep_service.py -- the Benchmark Lab's settings sweep. UI-free.

A sweep tunes transcription for one golden set in one language, following "Method" in
docs/specs/accuracy-tuning-plan.md:

- The set's clips are split by title, about 70% for tuning and 30% held out, so no title is in
  both halves. The hold-out only confirms a winner; it never picks one.
- The default setup is run twice on the tuning clips; the gap between the two is the noise.
- Coordinate descent over the plan's range table: the model first, then one setting at a time
  from the current best, keeping a value only when it beats the current best by more than the
  noise. After the model step every pass uses one model, so each model loads once.
- A winner other than the default is run beside the default on the hold-out. It is recommended
  only when it beats the default there by the margin (2 CER points and twice the noise) with no
  more failed clips. Nothing is applied: approve() saves it as a setting profile and records the
  decision in model_decisions, as model_reeval_service does for models.

Every pass is an ordinary Lab run (benchmark_sessions), made by benchmark_lab_service's own run
loop under its job id, so the Lab's refusals while a run is going, its cancel and its GPU queue
cover a sweep too. Stopping and resuming need no state of their own: a pass is skipped when a
finished run of the same setup, model build and app version already scored exactly the same clips
at the same reference versions under the same scoring rules with no failed clip, so starting the
sweep again redoes only what is missing.
The sweep's own record (set, language, status, recommendation, decision) is one app setting.
The method's code is shared with the translation sweep in benchmark_sweep_core.

Transcription runs on this PC's own models, so a sweep costs nothing and the monthly cap never
applies to it. Models are never downloaded: a model that is not on the PC is left out and says so.
"""
import functools
import hashlib
import json
import os

import db
import media_inspect
import whisper_models
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import benchmark_sweep_core as sweep_core
from services import (model_readiness_service, profile_service, series_defaults_service, settings_service,
                      transcribe_options_service, transcribe_speed_service, translate_preset_service)
from services.benchmark_case_service import backend_problem, ignored_options, model_identity
from services.benchmark_runs_service import setup_key
from lib.errors import DependencyUnavailableError, UnsupportedOperationError

STAGE = "transcription"
JOB_ID = sweep_core.JOB_ID
# The plan's adoption rule for transcription: 2 CER points and twice the measured noise.
MIN_SCORE_MARGIN = 0.02
DECISION_CAPABILITY = "transcription"

_MODELS = (("whisper", "large-v3"), ("whisper", "large-v3-turbo"))
_EXTRA_MODELS = {"ja": (("whisper", "kotoba-v2"), ("anime_whisper", None))}
_MODEL_LABELS = {"anime_whisper": "anime-whisper"}

# (option, label, values): the transcription rows of the plan's "Starting numbers and sweep ranges".
KNOBS = (
    ("beam_size", "Beam size", (1, 3, 5)),
    ("min_silence_ms", "Minimum silence (ms)", (200, 300, 500, 800)),
    ("vad_threshold", "Voice detection threshold", (0.35, 0.45, 0.5, 0.6)),
    ("hallucination_silence_sec", "Skip silences longer than (s)", (0, 1, 2)),
    ("difficult_audio", "Difficult audio", (False, True)),
    ("whisper_repeat_guard", "Repeat guard", (False, True)),
    ("separate_vocals_first", "Separate vocals first", (False, True)),
)
_KNOB_LABELS = {k: label for k, label, _ in KNOBS}
NOT_SWEPT = (
    "Initial prompt: a title builds it from its glossary, and the Lab has no glossary to build it from.",
    "Qwen context pad: not a Lab option.",
    "Line timing settings (speech pad, timing refinement, split limits): compare them in the Arena, "
    "which judges timing against a repeat of the default.",
    "Translation settings: not part of this sweep.",
)

# (GPU, CPU) seconds of work per second of audio when this PC has not measured the model
# (tools/asr_bench_recipes.py's guesses), the extra for vocal separation, and one model load.
_SPEED_GUESS = {"whisper": (0.12, 1.0), "anime_whisper": (0.15, 1.0)}
_SEPARATION_GUESS = (0.3, 1.5)
_LOAD_SECONDS = 20.0
_UNKNOWN_CLIP_SECONDS = 10.0

_durations = {}
_store = sweep_core.SweepRecord("benchmark_sweep")


def get_sweep() -> dict:
    """The latest sweep, with "interrupted" for one the app stopped during."""
    return sweep_core.get_sweep(_store, _record_applies)


def _record_applies(record: dict, winner: dict) -> list:
    return _applies(record["language"], winner)


# ---------------------------------------------------------------------------
# What a sweep runs
# ---------------------------------------------------------------------------

def _sweep_cases(set_name: str, language: str) -> list:
    """The set's scorable audio clips in this language: one language per sweep, because results
    are never averaged across Chinese, Japanese and Korean."""
    return [c for c in lab._select_cases(STAGE, set_name=set_name, scorable_only=True)
            if (c.get("source_language") or "zh") == language
            and c.get("reference_text") and c.get("input_filename")]


def _model_label(engine, model) -> str:
    return _MODEL_LABELS.get(engine) or model


def _model_choices(language: str, refine: bool) -> list:
    """Every model the range table names for this language, and why one is left out."""
    out = []
    for engine, model in _MODELS + _EXTRA_MODELS.get(language, ()):
        if engine == "whisper" and model is not None:
            reason = whisper_models.whisper_model_language_problem(model, language) or (
                None if whisper_models.is_whisper_model_cached(model) else
                f"{model} isn't downloaded on this PC. Run a title with it once; the sweep never downloads a model.")
        else:
            reason = backend_problem(engine, language, refine)
        out.append({"engine": engine, "model": model, "label": _model_label(engine, model),
                    "included": reason is None, "reason": reason})
    return out


def _knob_choices(use_gpu) -> list:
    out = []
    for key, label, values in KNOBS:
        reason = None
        if key == "separate_vocals_first" and not model_readiness_service.separation_ready("auto", use_gpu):
            reason = ("The vocal separation model isn't downloaded yet. Run Separate vocals first once on a "
                      "title; the sweep does not download it.")
        out.append({"key": key, "label": label, "values": list(values), "included": reason is None,
                    "reason": reason})
    return out


class _Plan:
    stage = STAGE
    unit = "clips"

    def __init__(self, set_name, language, use_gpu):
        self.set_name, self.language = set_name, language
        self.use_gpu = bool(settings_service.get("use_gpu")) if use_gpu is None else bool(use_gpu)
        cases = _sweep_cases(set_name, language)
        if not cases:
            raise UnsupportedOperationError(
                "This set has no audio clips with a reference in that language.")
        self.tuning, self.holdout = sweep_core.split_by_title(cases)
        self.defaults = transcribe_options_service.lab_option_defaults()
        default_model = transcribe_options_service.default_whisper_size()
        self.models = _model_choices(language, self.defaults["refine_timing"])
        if not any(m["included"] and m["model"] == default_model for m in self.models):
            raise DependencyUnavailableError(
                f"The default model, {default_model}, isn't on this PC yet. Run a title with it once; "
                "the sweep never downloads a model.")
        self.knobs = _knob_choices(self.use_gpu)
        self.default = {"engine": "whisper", "model": default_model, "changes": {}}
        cfg = self.config(self.default)
        context = lab.run_context(STAGE, cfg, cases, self.use_gpu)
        # The scoring rules a reused run must share (benchmark_runs_service._check_same_scoring), and
        # the app version, since a fix to transcription can change what the same setup produces.
        self.markers = {k: context.get(k) for k in ("text_normaliser", "script_normaliser", "app_version")}

    def config(self, setup: dict) -> dict:
        return lab.check_config(STAGE, {"engine": setup["engine"], "model": setup["model"],
                                        "options": setup["changes"]})

    def key(self, setup: dict) -> str:
        cfg = self.config(setup)
        return setup_key({"engine": cfg["engine"], "model": cfg["model"],
                          "context_settings": json.dumps({"transcribe_options": cfg["options"],
                                                          "use_gpu": self.use_gpu})})

    def identity(self, setup: dict) -> dict:
        return model_identity(STAGE, self.config(setup))

    def cases(self, half: str) -> list:
        return self.tuning if half == "tuning" else self.holdout

    def describe(self, setup: dict) -> str:
        return _describe(setup, self.defaults)

    def defaults_of(self, setup: dict) -> dict:
        return self.defaults

    def ignored_options(self, setup: dict):
        return ignored_options(setup["engine"])


def _describe(setup: dict, defaults: dict) -> str:
    parts = [_model_label(setup["engine"], setup["model"])]
    for key, value in setup["changes"].items():
        if value != defaults.get(key):
            parts.append(f"{_KNOB_LABELS.get(key, key)} {_value_text(value)}")
    return ", ".join(parts)


def _value_text(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _clip_seconds(case: dict) -> tuple:
    """(length in seconds, whether it was read) of a case's clip, cached per file and change time."""
    path = os.path.join(db.BENCHMARK_DIR, os.path.basename(case.get("input_filename") or ""))
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return _UNKNOWN_CLIP_SECONDS, False
    if (path, stamp) not in _durations:
        _durations[(path, stamp)] = media_inspect.duration_seconds(path)
    seconds = _durations[(path, stamp)]
    return (seconds, True) if seconds else (_UNKNOWN_CLIP_SECONDS, False)


def _pass_seconds(setup: dict, audio_seconds: float, plan: _Plan) -> float:
    gpu = 0 if plan.use_gpu else 1
    factor = _SPEED_GUESS[setup["engine"]][gpu]
    if setup["engine"] == "whisper":
        speed = transcribe_speed_service.measured_transcribe_speed(setup["model"], plan.use_gpu)
        if speed and speed > 0:
            factor = 1.0 / speed
    if setup["changes"].get("separate_vocals_first"):
        factor += _SEPARATION_GUESS[gpu]
    return audio_seconds * factor


def estimate(set_name: str, language: str, use_gpu: bool | None = None) -> dict:
    """What a sweep would run before it starts: the two halves, the models and settings it covers
    (and why any is left out), the passes already finished that it will skip, and a rough time."""
    set_name = sweep_core.check_set_and_language(set_name, language)
    plan = _Plan(set_name, language, use_gpu)
    lengths = {}
    guessed = False
    for half in ("tuning", "holdout"):
        total = 0.0
        for c in plan.cases(half):
            seconds, read = _clip_seconds(c)
            total += seconds
            guessed = guessed or not read
        lengths[half] = total
    todo, done = sweep_core.passes_left(plan)
    models = set()
    seconds = 0.0
    for setup, half in todo:
        seconds += _pass_seconds(setup, lengths[half], plan)
        models.add((setup["engine"], setup["model"]))
    seconds += _LOAD_SECONDS * len(models)
    lab_cost = lab.estimate(STAGE, [plan.config(plan.default)], set_name=set_name,
                            case_ids=[c["id"] for c in plan.tuning + plan.holdout])
    return {
        "set_name": set_name, "language": language, "use_gpu": plan.use_gpu,
        "tuning_cases": len(plan.tuning), "holdout_cases": len(plan.holdout),
        "tuning_titles": len({sweep_core.title_of(c) for c in plan.tuning}),
        "holdout_titles": len({sweep_core.title_of(c) for c in plan.holdout}),
        "audio_seconds": round(lengths["tuning"] + lengths["holdout"], 1), "durations_guessed": guessed,
        "small_set": sweep_core.is_small_set(plan), "min_set_cases": sweep_core.MIN_SET_CASES,
        "models": plan.models, "knobs": plan.knobs, "not_swept": list(NOT_SWEPT),
        "passes_done": done, "passes_to_run": len(todo), "estimated_seconds": round(seconds),
        "estimated_cost_usd": lab_cost["estimated_cost_usd"], "monthly_cap_usd": lab_cost["monthly_cap_usd"],
        "monthly_refusal": lab_cost["monthly_refusal"],
    }


# ---------------------------------------------------------------------------
# Starting and running
# ---------------------------------------------------------------------------

def start(set_name: str, language: str, use_gpu: bool | None = None) -> dict:
    """Starts (or resumes) a sweep as the Lab's background job. Passes that already have a
    finished run are skipped, so starting a stopped sweep again picks up where it stopped."""
    est = estimate(set_name, language, use_gpu)
    if est["monthly_refusal"]:
        raise UnsupportedOperationError(est["monthly_refusal"])
    record = {"id": hashlib.sha1(f"{est['set_name']}|{language}|{est['use_gpu']}".encode()).hexdigest()[:12],
              "set_name": est["set_name"], "language": language, "use_gpu": est["use_gpu"],
              "status": "queued", "started_at": sweep_core.now(), "finished_at": None, "note": None,
              "passes_run": 0, "passes_reused": 0, "recommendation": None, "decision": None,
              "estimate": {k: est[k] for k in ("tuning_cases", "holdout_cases", "passes_to_run",
                                                 "estimated_seconds")}}
    return sweep_core.launch(_store, record, lambda: job_launch.start_job(
        JOB_ID, _run_job, JOB_ID, est["set_name"], language, est["use_gpu"], est["passes_to_run"],
        gpu_touching=True, description="Benchmark sweep", line_fields=()))


def _run_job(job_id, set_name, language, use_gpu, planned):
    def prepare():
        plan = _Plan(set_name, language, use_gpu)
        loaded: dict[str, tuple | None] = {"model": None}

        def run_pass(setup, half, step, created, done, planned):
            model = (setup["engine"], setup["model"])
            if loaded["model"] not in (None, model):
                # The model cache never evicts: on a small card the next model would run out of memory.
                whisper_models.release_gpu_models()
            loaded["model"] = model
            return sweep_core.run_pass(job_id, plan, setup, half, step, created, done, planned, plan.use_gpu)
        return plan, run_pass

    # The job runs in the API process: models it loaded would otherwise stay on the GPU.
    sweep_core.run_sweep(job_id, _store, planned, prepare, _recommend,
                   cleanup=lambda: whisper_models.release_gpu_models())


# ---------------------------------------------------------------------------
# The recommendation and the owner's decision
# ---------------------------------------------------------------------------

def _margin_rule(noise: float, margin: float) -> str:
    return (f"A change is recommended only when it beats the default on the held-out titles by "
            f"at least {MIN_SCORE_MARGIN * 100:g} CER points and at least {sweep_core.noise_factor_text()} "
            f"the noise ({noise * 100:.1f} points), "
            f"so here by {margin * 100:.1f} points, with no more failed clips.")


def _recommend(plan: _Plan, outcome: dict) -> dict:
    return sweep_core.recommend(plan, outcome, MIN_SCORE_MARGIN, _margin_rule, "clips")


def _profile_options(winner: dict) -> dict:
    options = {"asr_backend_choice": winner["engine"]}
    if winner["engine"] == "whisper":
        options["whisper_size"] = winner["model"]
    options.update(winner["changes"])
    return options


# What save_translate_preset stores besides the options: the column defaults of a new profile.
_PROFILE_TOGGLES = {"include_genre_notes": True, "default_female_pronouns": False}


def _applies(language: str, winner: dict) -> list:
    """Exactly what applying the approved profile writes on a title of this language, in the
    words of the title's settings, so the owner sees it before approving. Worked out by the
    same plan the apply route runs, from the profile as it would be saved."""
    engine = settings_service.get("default_engine")
    options = profile_service.validate_options(_profile_options(winner))
    preset = {"translation_engine": engine, "engine_model": None, "style_preset": None,
              "options_json": profile_service.dump_options(options),
              **{k: int(v) for k, v in _PROFILE_TOGGLES.items()}}
    columns = profile_service.plan(preset, {"source_language": language})["columns"]
    out = []
    for key, value in columns.items():
        if key == "translate_start_json":
            continue
        spec = series_defaults_service.SETTINGS.get(key, {})
        if spec.get("type") == "bool":
            value = bool(value)
        out.append({"key": key, "label": spec.get("label", key), "value": _value_text(value)})
    out.append({"key": "translate_start", "label": "Translate form starting style and model",
                "value": "back to the engine's defaults"})
    return out


def decide(decision: str, reason: str | None = "") -> dict:
    """The owner's call on the finished sweep's recommendation (benchmark_sweep_core.decide)."""
    return sweep_core.decide(_store, decision, reason, applies=_record_applies, save_profile=_save_profile,
                       capability=DECISION_CAPABILITY, source="settings sweep")


def _save_profile(name: str, winner: dict) -> dict:
    return translate_preset_service.save_translate_preset(
        name, str(settings_service.get("default_engine")), options=_profile_options(winner),
        include_genre_notes=_PROFILE_TOGGLES["include_genre_notes"],
        default_female_pronouns=_PROFILE_TOGGLES["default_female_pronouns"])


def list_decisions() -> dict:
    """Earlier sweep decisions, newest first."""
    return sweep_core.list_decisions(DECISION_CAPABILITY, functools.partial(
        _decided_setup, transcribe_options_service.lab_option_defaults()))


def _decided_setup(defaults: dict, engine, setup: dict) -> str:
    return _describe({"engine": engine, "model": setup.get("model"), "changes": setup.get("changes") or {}},
                     defaults)
