"""
services/benchmark_translate_sweep_service.py -- the Benchmark Lab's settings sweep for the
translation stage. Same method as the transcription sweep (benchmark_sweep_service): one golden
set and one language, split by whole titles, the default run twice for noise, one setting at a
time from the current best, a hold-out to confirm, nothing applied until the owner approves. It
varies the model, batch size, context window, Reflect and style preset of one engine; every pass
is an ordinary Lab run of scene-batched arms (benchmark_scene_service), so cancel, the monthly
cap and the paid-engine rules are the Lab's own. The method's code is benchmark_sweep_core.
"""

import hashlib
import json

import db
import translate_engines
import translation_guide
from engine_backends.engine_registry import CAP_INSTRUCTIONS, engine_capabilities
from engine_backends.temperature import TEMPERATURE_ENGINES
from jobs import job_launch
from services import benchmark_lab_service as lab
from services import benchmark_scene_service as scene
from services import benchmark_sweep_core as sweep_core
from services import settings_service, translate_preset_service, translate_service
from services.benchmark_case_service import model_identity
from services.benchmark_runs_service import setup_key
from services.service_errors import InvalidInputError, MissingKeyError, UnsupportedOperationError

STAGE = "translation"
JOB_ID = sweep_core.JOB_ID
# The plan's adoption rule for translation: 1.5 chrF points, and (as for transcription) twice the noise.
MIN_SCORE_MARGIN = 0.015
COST_MARGIN = 2.0
COST_MARGIN_FLOOR_USD = 0.05
# Not "translation": that capability is model re-evaluation's list of candidate models.
DECISION_CAPABILITY = "translation_settings"

# (option, label, values): the translation rows of the plan's "Starting numbers and sweep ranges".
# Context before and after are swept one at a time, like every other setting.
KNOBS = (
    ("batch_size", "Batch size", (15, 30, 60)),
    ("context_window", "Context lines before", (3, 6, 10, 20)),
    ("context_window_ahead", "Context lines after", (1, 3, 6, 10)),
    ("reflect", "Reflect", (False, True)),
    ("style_preset", "Style preset", None),
    ("memory_examples", "Translation-memory examples", (False, True)),
    ("temperature", "Temperature", (None, 0.3)),
)
_LABELS = {k: label for k, label, _ in KNOBS}
# Options a title's Translate has no field for, so an approved profile cannot carry them.
PROFILE_UNSAVED = ("memory_examples", "temperature")
NOT_SWEPT = (
    "The judge score: the sweep compares chrF only. Check a winner with the judge in the Arena before approving.",
)

_store = sweep_core.SweepRecord("benchmark_translate_sweep")


def get_sweep() -> dict:
    """The latest translation sweep, with "interrupted" for one the app stopped during."""
    return sweep_core.get_sweep(_store, _record_applies)


def _record_applies(record: dict, winner: dict) -> list:
    return _applies(winner)


# ---------------------------------------------------------------------------
# What a sweep runs
# ---------------------------------------------------------------------------

def _check_params(set_name, language, engine, model, series_id):
    set_name = sweep_core.check_set_and_language(set_name, language)
    if not isinstance(engine, str):
        raise InvalidInputError("Pick a translation engine.")
    # Checks the engine and model the way a Lab run does.
    lab.check_config(STAGE, {"engine": engine, "model": model,
                             "options": {"series_id": series_id} if series_id else None})
    return set_name


def _sweep_cases(set_name: str, language: str) -> list:
    """The set's scorable translation cases in this language: one language per sweep, because
    results are never averaged across Chinese, Japanese and Korean."""
    return [c for c in lab._select_cases(STAGE, set_name=set_name, scorable_only=True)
            if (c.get("source_language") or "zh") == language and c.get("reference_text")
            and (c.get("source_text") or "").strip()]


class _Plan:
    stage = STAGE

    def __init__(self, set_name, language, engine, model, series_id):
        self.set_name, self.language, self.engine, self.series_id = set_name, language, engine, series_id
        cases = _sweep_cases(set_name, language)
        if not cases:
            raise UnsupportedOperationError("This set has no translation cases with a reference in that language.")
        self.tuning, self.holdout = sweep_core.split_by_title(cases)
        self.default_model = model or translate_engines.effective_default_model(engine)
        self.default = {"engine": engine, "model": model, "changes": {}}
        entry = next(e for e in translate_service.list_engines() if e["name"] == engine)
        self.models = [{"engine": engine, "model": m, "label": m, "included": True, "reason": None}
                       for m in (entry["models"] or []) if m != self.default_model]
        self.knobs = [{"key": key, "label": label, "included": True, "reason": None,
                       "values": list(values if values is not None else translation_guide.STYLE_PRESETS)}
                      for key, label, values in KNOBS
                      if (key != "reflect" or CAP_INSTRUCTIONS in engine_capabilities(engine))
                      and (key != "memory_examples" or series_id)
                      and (key != "temperature" or engine in TEMPERATURE_ENGINES)]
        self._identity, self._configs = {}, {}
        context = lab.run_context(STAGE, self.config(self.default), cases, False)
        # What a reused run must share: the app version, since a fix to translation can change what
        # the same setup produces.
        self.markers = {"app_version": context.get("app_version")}

    def model_of(self, setup: dict) -> str:
        return setup["model"] or self.default_model

    def with_model(self, setup: dict, model: str) -> dict:
        """The setup on another model; the default model stays as the sweep was asked for it."""
        return {**setup, "model": self.default["model"] if model == self.default_model else model}

    def defaults_of(self, setup: dict) -> dict:
        return scene.defaults(setup["engine"], setup["model"])

    def ignored_options(self, setup: dict):
        return ()

    def describe(self, setup: dict) -> str:
        return _describe(self, setup)

    def options(self, setup: dict) -> dict:
        return {**setup["changes"], **({"series_id": self.series_id} if self.series_id else {})}

    def config(self, setup: dict) -> dict:
        # Checking an arm lists the engines (package and key probes), and the descent asks for the
        # same few dozen setups many times.
        memo = json.dumps(setup, sort_keys=True)
        if memo not in self._configs:
            self._configs[memo] = lab.check_config(STAGE, {"engine": setup["engine"], "model": setup["model"],
                                                            "options": self.options(setup)})
        return self._configs[memo]

    def key(self, setup: dict) -> str:
        cfg = self.config(setup)
        return setup_key({"engine": cfg["engine"], "model": self.model_of(setup),
                          "context_settings": json.dumps({"translate_options": scene.stored_options(cfg["options"])})})

    def identity(self, setup: dict) -> dict:
        model = self.model_of(setup)
        if model not in self._identity:
            self._identity[model] = model_identity(STAGE, self.config(setup))
        return self._identity[model]

    def cases(self, half: str) -> list:
        return self.tuning if half == "tuning" else self.holdout


def _value_text(value) -> str:
    if value is None:
        return "default"
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _describe(plan: _Plan, setup: dict) -> str:
    parts = [plan.model_of(setup)]
    defaults = plan.defaults_of(setup)
    parts += [f"{_LABELS.get(k, k)} {_value_text(v)}" for k, v in setup["changes"].items() if v != defaults.get(k)]
    return ", ".join(parts)


def _pass_cost(plan: _Plan, setup: dict, half: str, ceiling_model: str) -> float:
    """What one pass is expected to cost. Passes that can run on a model the sweep may pick use the
    dearest one, so the estimate is never lower than what the sweep can spend."""
    if setup["changes"] or half == "holdout":
        setup = plan.with_model(setup, ceiling_model)
    return lab._estimate_config(plan.config(setup), plan.cases(half))


def estimate(set_name: str, language: str, engine: str, model: str = None, series_id: int = None) -> dict:
    """What a sweep would run before it starts: the two halves, the settings it covers, the passes
    already finished that it will skip, and what the paid passes would cost against the monthly cap."""
    set_name = _check_params(set_name, language, engine, model, series_id)
    plan = _Plan(set_name, language, engine, model, series_id)
    todo, done = sweep_core.passes_left(plan)
    models = [plan.default_model] + [m["model"] for m in plan.models]
    ceiling = max(models, key=lambda m: _pass_cost(plan, plan.with_model(plan.default, m), "tuning", m))
    total = sum(_pass_cost(plan, setup, half, ceiling) for setup, half in todo)
    monthly_cap, spend = settings_service.get_monthly_cap_usd(), db.get_month_spend()
    capped = lab._cap_applies(engine)
    remaining, refusal = translate_engines.resolve_cost_cap(None, monthly_cap, spend) if capped else (None, None)
    return {
        "set_name": set_name, "language": language, "engine": engine, "model": plan.default_model,
        "series_id": series_id, "tuning_cases": len(plan.tuning), "holdout_cases": len(plan.holdout),
        "tuning_titles": len({sweep_core.title_of(c) for c in plan.tuning}),
        "holdout_titles": len({sweep_core.title_of(c) for c in plan.holdout}),
        "small_set": sweep_core.is_small_set(plan), "min_set_cases": sweep_core.MIN_SET_CASES,
        "models": plan.models, "knobs": plan.knobs, "not_swept": list(NOT_SWEPT),
        "passes_done": done, "passes_to_run": len(todo), "estimated_cost_usd": round(total, 6),
        "cap_applies": capped, "monthly_cap_usd": monthly_cap, "month_spend_usd": round(spend, 6),
        "remaining_usd": None if remaining is None else round(remaining, 6), "monthly_refusal": refusal,
        "estimate_above_cap": bool(remaining is not None and total > remaining),
    }


# ---------------------------------------------------------------------------
# Starting and running
# ---------------------------------------------------------------------------

def start(set_name: str, language: str, engine: str, model: str = None, series_id: int = None) -> dict:
    """Starts (or resumes) a translation sweep as the Lab's background job. The estimate is
    worked out here, whatever the client showed: a sweep over the monthly cap is refused, and a
    pass that finished before is skipped, so starting a stopped sweep picks up where it stopped."""
    est = estimate(set_name, language, engine, model, series_id)
    if est["monthly_refusal"]:
        raise UnsupportedOperationError(est["monthly_refusal"])
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            f"This sweep is estimated at ${est['estimated_cost_usd']:.4f}, more than the "
            f"${est['remaining_usd']:.4f} left of this month's cap.")
    if translate_service.resolve_api_key(engine) is None:
        raise MissingKeyError(engine)
    record = {"id": hashlib.sha1(f"{est['set_name']}|{language}|{engine}|{est['model']}|{series_id}".encode()).hexdigest()[:12],
              "set_name": est["set_name"], "language": language, "engine": engine, "model": est["model"],
              "series_id": series_id, "status": "queued", "started_at": sweep_core.now(), "finished_at": None,
              "note": None, "passes_run": 0, "passes_reused": 0, "recommendation": None, "decision": None,
              "estimate": {k: est[k] for k in ("tuning_cases", "holdout_cases", "passes_to_run")}}
    # The estimate leaves out re-asks and retries and counts Reflect as a flat three, so the sweep as
    # a whole may spend a margin above it, and no more.
    job_cap = round(est["estimated_cost_usd"] * COST_MARGIN + COST_MARGIN_FLOOR_USD, 6) if est["cap_applies"] else None
    gpu = any(translate_engines.ollama_touches_local_gpu(engine, m["model"]) for m in
              [{"model": est["model"]}, *est["models"]])
    return sweep_core.launch(_store, record, lambda: job_launch.start_job(
        JOB_ID, _run_job, JOB_ID, est["set_name"], language, engine, model, series_id,
        est["passes_to_run"], job_cap, gpu_touching=gpu, description="Translation sweep", line_fields=()))


def _run_job(job_id, set_name, language, engine, model, series_id, planned, job_cap=None):
    def prepare():
        plan = _Plan(set_name, language, engine, model, series_id)
        api_key = translate_service.resolve_api_key(engine)
        if api_key is None:
            raise MissingKeyError(engine)

        def run_pass(setup, half, step, created, done, planned):
            left = None if job_cap is None else max(job_cap - sum(
                (db.get_benchmark_session(c) or {}).get("total_cost_usd") or 0.0 for c in created), 0.0)
            return sweep_core.run_pass(job_id, plan, setup, half, step, created, done, planned, False,
                                 api_key, left)
        return plan, run_pass

    sweep_core.run_sweep(job_id, _store, planned, prepare, _recommend, reject=_reject_failed_cases)


def _reject_failed_cases(plan, setup, half, run):
    # The same rule as benchmark_sweep_core.finished_runs, so a winner is never picked from a pass
    # a resume would not accept (and pay for again).
    if run.get("error_count"):
        return f"The pass {_describe(plan, setup)} ({half}) had {run['error_count']} failed cases."
    return None


# ---------------------------------------------------------------------------
# The recommendation and the owner's decision
# ---------------------------------------------------------------------------

def _margin_rule(noise: float, margin: float) -> str:
    return (f"A change is recommended only when it beats the default on the held-out titles by "
            f"at least 1.5 chrF points and at least twice the noise ({noise * 100:.1f} points), "
            f"so here by {margin * 100:.1f} points, with no more failed cases. The judge "
            "score is not part of the sweep.")


def _recommend(plan: _Plan, outcome: dict) -> dict:
    def savable(winner):
        # A profile that would equal the default has nothing to approve.
        return bool(set(winner["changes"]) - set(PROFILE_UNSAVED)) or winner["model"] != plan.default["model"]
    return sweep_core.recommend(plan, outcome, MIN_SCORE_MARGIN, _margin_rule, "cases", savable=savable)


def _profile_fields(winner: dict) -> tuple:
    """(engine model, style preset, run options) a profile saves for the winner. The model is
    left empty for an engine without a model picker, as a profile requires."""
    options = scene.resolve_options(winner["engine"], winner["model"], winner["changes"])
    run = {k: options[k] for k in ("batch_size", "context_window", "context_window_ahead", "reflect")}
    entry = next((e for e in translate_service.list_engines() if e["name"] == winner["engine"]), {})
    return (winner["model"] if entry.get("models") else None), options["style_preset"], run


def _applies(winner: dict) -> list:
    """What the approved profile holds, in the words of the Translate form, shown before approving."""
    model, style, run = _profile_fields(winner)
    rows = [("engine", "Engine", winner["engine"]), ("model", "Model", model or "the engine's default"),
            ("style_preset", "Style preset", style)]
    rows += [(k, scene.OPTION_LABELS[k], _value_text(v)) for k, v in run.items()]
    return [{"key": k, "label": label, "value": value} for k, label, value in rows]


def decide(decision: str, reason: str = "") -> dict:
    """The owner's call on the finished sweep's recommendation (benchmark_sweep_core.decide)."""
    return sweep_core.decide(_store, decision, reason, applies=_record_applies, save_profile=_save_profile,
                       capability=DECISION_CAPABILITY, source="translation sweep")


def _save_profile(name: str, winner: dict) -> dict:
    model, style, run = _profile_fields(winner)
    return translate_preset_service.save_translate_preset(
        name, winner["engine"], engine_model=model, style_preset=style, options=run)


def list_decisions() -> dict:
    """Earlier translation sweep decisions, newest first."""
    return sweep_core.list_decisions(DECISION_CAPABILITY, _decided_setup)


def _decided_setup(engine, setup: dict) -> str:
    return ", ".join([setup.get("model") or engine or "",
                      *(f"{_LABELS.get(k, k)} {_value_text(v)}" for k, v in (setup.get("changes") or {}).items())])
