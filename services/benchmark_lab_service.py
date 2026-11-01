"""
services/benchmark_lab_service.py -- the Benchmark Lab. UI-free.

Builds on the earlier benchmark (benchmark.py's runners and scoring, the
benchmark_cases table) rather than beside it:

- Golden sets. Every case has a tier -- "public" (an imported public test
  subset, e.g. a FLORES-200 zho->eng slice), "application" (your own
  corrected translations) or "regression" (a line you fixed by hand and
  explicitly chose to keep as a test) -- and a set name. Importing reads
  text the user supplies (JSONL or tab-separated); nothing is downloaded.
- Persistent per-run records. Each run is one benchmark_sessions row (stage,
  engine, model, prompt version, context settings, aggregate score, average
  latency, cost, peak VRAM) with one benchmark_results row per case, so a
  model or prompt change is provably better or worse. The older
  benchmark_runs history (an older run-over-run check) is not written to.
- Model Arena. Starting a run with two or more configs runs the same cases
  through each in turn under one arena_group; arena() lines their results
  up case by case. This replaces the "compare engines on one case"
  check with a persisted, whole-set version.
- Metrics (item 11): translation scores are benchmark.translation_similarity;
  transcription and OCR use 1 - CER (character error rate), or 1 - WER for
  a transcript in a space-delimited language. Each result records its
  metric so a WER is never read as a translation score. One aggregate
  score per run plus per-example pass/fail (item 8: no rubric sliders).
  Translation similarity is chrF when sacrebleu is installed (metric "chrf",
  passes at CHRF_PASS_THRESHOLD) and the difflib ratio when it isn't (metric
  "similarity", passes at PASS_THRESHOLD); older results keep "similarity".
  CER/WER use jiwer when it is installed (lower-cased and whitespace
  normalised on both texts; punctuation removed for transcripts but kept
  for OCR, where it is part of what was read) and the built-in scorer when it
  isn't; each result records its scorer, since the two can differ.
- Money. A run can spend on paid engines, so estimate() is shown first and
  start_run() refuses when the monthly cap is used up or the estimate is
  over what is left; while running, each paid engine stops at the cap.
  Spend is written to usage_log (operation "benchmark") so it counts
  toward the monthly cap like any other run.

Keys are resolved server-side (translate_service.resolve_api_key) and never
returned; errors are passed through shared.redact_secrets before
they are stored.

Not built here: scoped translation-memory saves,
the auto-derived Translation Profile and a COMET scorer.
"""
import whisper_models
from lib import clock
import json
import ocr
import os
import uuid

import background_jobs
from jobs import job_launch
import benchmark
import db
from engine_backends import engine_registry, pricing, local
from core import SOURCE_LANGUAGES
from engine_backends import llm_tasks, paid_calls
from engine_backends.run_limits import FailureStreak, RateLimitWaitBudget, _rate_limit_budget_var
from services import settings_service, transcribe_options_service, translate_run_service, translate_service
from services.benchmark_case_service import (LAB_BACKENDS, app_version, ignored_options, model_identity, redact,
                                                   whisper_options,
                                                   run_ocr as _run_ocr,
                                                   run_transcription as _run_transcription,
                                                   run_translation as _run_translation)
from services import benchmark_end_to_end_service as end_to_end
from services import benchmark_hardsub_read_service as hardsub
from services import benchmark_runs_service
from services import benchmark_vision_service as vision
from services import benchmark_scene_service as scene_service
from services.benchmark_runs_service import MAX_CONFIGS, arena, close_stale_runs, get_run, session_out as _session_out  # noqa: F401
from services.benchmark_scoring_service import (CHRF_PASS_THRESHOLD, MAX_TEXT_CHARS, PASS_THRESHOLD, pass_threshold,
                                                 script_normaliser)
from lib.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    UnsupportedOperationError,
)

JOB_ID = "benchmark_lab"
TIERS = ("public", "application", "regression")
STAGES = ("translation", "transcription", "ocr")
REGRESSION_SET = "regressions"
MAX_IMPORT_CASES = 500
MAX_IMPORT_BYTES = 2_000_000
MAX_LABEL_CHARS = 120
MAX_SET_NAME_CHARS = 60
OCR_BACKENDS = tuple(b for b in ocr.OCR_BACKEND_OPTIONS if b != "auto")


# ---------------------------------------------------------------------------
# Cases and golden sets
# ---------------------------------------------------------------------------

def _clean_text(value, name: str, required: bool = True, max_len: int = MAX_TEXT_CHARS) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"{name} can't be empty.")
    if len(value) > max_len:
        raise InvalidInputError(f"{name} is too long (at most {max_len} characters).")
    return value


def _check_tier(tier: str) -> str:
    if tier not in TIERS:
        raise InvalidInputError(f"tier must be one of {', '.join(TIERS)}.")
    return tier


def _check_language(lang: str) -> str:
    if lang not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"source_language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    return lang


def _stored_case_out(case_id: int) -> dict:
    case = db.get_benchmark_case(case_id)
    if case is None:
        raise NotFoundError("Benchmark case not found.")
    return _case_out(case)


def _case_out(c: dict) -> dict:
    """A case for the API: never the stored input file name or path."""
    return {
        "id": c["id"], "label": c.get("label") or "", "stage": c.get("stage"),
        "tier": c.get("tier") or "application", "set_name": c.get("set_name") or "",
        "source_language": c.get("source_language") or "zh",
        "source_text": c.get("source_text"), "reference_text": c.get("reference_text"),
        "has_reference": bool(c.get("reference_text")),
        "has_input_file": bool(c.get("input_filename")), "hard_sub": hardsub.load(c) is not None,
        "origin_drama_id": c.get("origin_drama_id"), "origin_line_id": c.get("origin_line_id"),
        "title": c.get("title") or "", "tags": _tags(c.get("tags")),
        "scene_order": c.get("scene_order"), "excluded": bool(c.get("excluded")),
        "ref_version": c.get("ref_version") or 1,
        "has_ref_times": c.get("ref_start") is not None and c.get("ref_end") is not None,
        "created_at": c.get("created_at"),
    }


def _tags(raw) -> list:
    try:
        tags = json.loads(raw or "[]")
    except ValueError:
        return []
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def _select_cases(stage: str | None, tier: str | None = None, set_name: str | None = None, case_ids=None,
                  scorable_only: bool = False) -> list:
    """The cases matching a filter. A run passes scorable_only: an excluded
    case drops out of scoring but is still listed, so it can be brought back."""
    cases = db.list_benchmark_cases(stage)
    if scorable_only:
        cases = [c for c in cases if not c.get("excluded")]
    if tier:
        cases = [c for c in cases if (c.get("tier") or "application") == tier]
    if set_name:
        cases = [c for c in cases if (c.get("set_name") or "") == set_name]
    if case_ids is not None:
        if not case_ids:
            raise InvalidInputError("case_ids can't be an empty list.")
        wanted = set(case_ids)
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def list_cases(stage: str | None = None, tier: str | None = None, set_name: str | None = None) -> dict:
    if stage is not None and stage not in STAGES:
        raise InvalidInputError("Unknown stage.")
    if tier is not None:
        _check_tier(tier)
    return {"cases": [_case_out(c) for c in _select_cases(stage, tier, set_name)]}


def list_sets() -> dict:
    """One row per (stage, tier, set name) with its case count."""
    groups = {}
    for c in db.list_benchmark_cases():
        key = (c.get("stage"), c.get("tier") or "application", c.get("set_name") or "")
        g = groups.setdefault(key, {"stage": key[0], "tier": key[1], "set_name": key[2],
                                    "case_count": 0, "with_reference": 0, "excluded": 0})
        g["case_count"] += 1
        g["excluded"] += 1 if c.get("excluded") else 0
        g["with_reference"] += 1 if c.get("reference_text") else 0
    return {"sets": sorted(groups.values(), key=lambda g: (g["stage"] or "", g["tier"], g["set_name"]))}


def create_case(label: str, source_text: str, reference_text: str | None = None,
                source_language: str = "zh", tier: str = "application",
                set_name: str = "") -> dict:
    """A translation case typed in by hand. (Audio/image cases still come
    from files registered on the PC.)"""
    label = _clean_text(label, "label", max_len=MAX_LABEL_CHARS)
    source_text = _clean_text(source_text, "source_text")
    reference_text = _clean_text(reference_text, "reference_text", required=False) or None
    _check_language(source_language)
    _check_tier(tier)
    set_name = _clean_text(set_name, "set_name", required=False, max_len=MAX_SET_NAME_CHARS)
    if set_name and db.find_benchmark_case(set_name, source_text):
        raise ConflictError("That set already has a case with this source text.")
    case_id = db.create_benchmark_lab_case(label, "translation", source_language, source_text,
                                           reference_text, tier, set_name)
    return _stored_case_out(case_id)


def _job_active() -> bool:
    st = background_jobs.get_status(JOB_ID)
    return bool(st and st.get("status") in ("running", "queued"))


def _refuse_while_running():
    if _job_active():
        raise ConflictError("A benchmark run is going; wait for it to finish.")


def delete_case(case_id: int) -> dict:
    _refuse_while_running()
    case = db.get_benchmark_case(case_id)
    if not case:
        raise NotFoundError("Benchmark case not found.")
    db.delete_benchmark_case(case_id)
    hardsub.remove_case_files(case)
    return {"deleted": True, "id": case_id}


def _parse_import(fmt: str, text: str) -> list:
    """[(source, reference or None, label or None)] from JSONL (one object
    per line with "source"/"reference", optionally "id"/"label") or TSV
    (source<TAB>reference per line). Blank lines and # comments are skipped."""
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if fmt == "jsonl":
            try:
                obj = json.loads(line)
            except ValueError:
                raise InvalidInputError(f"Line {n} isn't valid JSON.") from None
            if not isinstance(obj, dict):
                raise InvalidInputError(f"Line {n} isn't a JSON object.")
            source, ref = obj.get("source"), obj.get("reference")
            label = obj.get("label") or obj.get("id")
        else:
            parts = raw.rstrip("\r\n").split("\t")
            source, ref, label = parts[0], (parts[1] if len(parts) > 1 else None), None
        if not isinstance(source, str) or not source.strip():
            raise InvalidInputError(f"Line {n} has no source text.")
        if ref is not None and not isinstance(ref, str):
            raise InvalidInputError(f"Line {n}'s reference isn't text.")
        rows.append((_clean_text(source, f"Line {n} source"),
                     _clean_text(ref, f"Line {n} reference", required=False) or None,
                     str(label)[:MAX_LABEL_CHARS] if label is not None else None))
        if len(rows) > MAX_IMPORT_CASES:
            raise InvalidInputError(f"At most {MAX_IMPORT_CASES} cases per import.")
    if not rows:
        raise InvalidInputError("Nothing to import.")
    return rows


def import_golden_set(set_name: str, text: str, fmt: str = "jsonl", tier: str = "public",
                      source_language: str = "zh") -> dict:
    """Adds translation cases to a golden set from text the user pasted or
    read from a file on the PC (e.g. a FLORES-200 or WMT subset they
    downloaded). An example already in the set (same source) is skipped."""
    set_name = _clean_text(set_name, "set_name", max_len=MAX_SET_NAME_CHARS)
    _check_tier(tier)
    _check_language(source_language)
    if fmt not in ("jsonl", "tsv"):
        raise InvalidInputError("format must be jsonl or tsv.")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise InvalidInputError("The import is too large.")
    rows = _parse_import(fmt, text)
    added = skipped = 0
    for i, (source, ref, label) in enumerate(rows, 1):
        if db.find_benchmark_case(set_name, source):
            skipped += 1
            continue
        db.create_benchmark_lab_case(label or f"{set_name} #{i}", "translation", source_language,
                                     source, ref, tier, set_name)
        added += 1
    return {"set_name": set_name, "tier": tier, "added": added, "skipped": skipped}


def add_regression_case(drama_id: int, line_id: int) -> dict:
    """"Add as regression test": keeps a hand-fixed line (its source and the
    corrected translation now on it) as a regression case, so the next
    translation run includes it. Only ever called on an explicit request;
    nothing adds regressions automatically. Adding the same line again
    updates its reference instead of adding a duplicate."""
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    line = next((ln for ln in db.load_lines(drama_id) if ln.get("id") == line_id), None)
    if line is None:
        raise NotFoundError("Line not found.")
    source = (line.get("zh") or "").strip()
    fixed = (line.get("en") or "").strip()
    if not source or not fixed:
        raise UnsupportedOperationError("The line needs both source text and a translation.")
    source = source[:MAX_TEXT_CHARS]
    fixed = fixed[:MAX_TEXT_CHARS]
    drama_lang = drama.get("source_language")
    lang = drama_lang if isinstance(drama_lang, str) and drama_lang in SOURCE_LANGUAGES else "zh"
    existing = db.find_benchmark_case(REGRESSION_SET, source, origin_line_id=line_id)
    if existing:
        # Updated in place, so earlier runs' results stay tied to this case.
        _refuse_while_running()
        db.update_benchmark_case_texts(existing["id"], source, fixed)
        return {"case": _stored_case_out(existing["id"]), "replaced": True}
    title = drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}"
    case_id = db.create_benchmark_lab_case(
        f"{title[:80]} · line {line.get('idx', 0) + 1}", "translation", lang, source, fixed,
        "regression", REGRESSION_SET, content_type=drama.get("content_mode") or "audio_drama",
        origin_drama_id=drama_id, origin_line_id=line_id)
    return {"case": _stored_case_out(case_id), "replaced": bool(existing)}


# ---------------------------------------------------------------------------
# Options, estimate, run
# ---------------------------------------------------------------------------

def get_options() -> dict:
    return {
        "stages": list(STAGES), "tiers": list(TIERS), "source_languages": list(SOURCE_LANGUAGES),
        "translation_engines": translate_service.list_engines(),
        **whisper_options(),
        "transcribe_defaults": transcribe_options_service.lab_option_defaults(),
        "translate_choices": scene_service.option_choices(),
        "transcribe_options_ignored": {name: ignored_options(name) for name in ("whisper", *LAB_BACKENDS)},
        "ocr_backends": list(OCR_BACKENDS), "vision_engines": list(vision.VISION_ENGINES),
        "pass_threshold": PASS_THRESHOLD, "chrf_pass_threshold": CHRF_PASS_THRESHOLD,
        "max_configs": MAX_CONFIGS,
    }


def _cap_applies(engine: str) -> bool:
    return translate_run_service.engine_cap_applies(engine, bool(settings_service.get("gemini_free_tier")))


_NO_MODEL_FOLDER = ("The offline model folder isn't set or can't be found. "
                    "Set Settings > Offline Whisper model folder first.")


def _offline_model_folder():
    """The saved Settings folder if it exists, else None. Read here, never
    taken from the request, so a client can't name a path or repo id."""
    folder = settings_service.get("whisper_model_path") or ""
    return folder if isinstance(folder, str) and folder and os.path.isdir(folder) else None


def _run_file_case(stage, cfg, case, use_gpu, folder=None):
    if stage != "transcription":
        return _run_ocr(cfg, case)
    if cfg.get("model") == benchmark.LOCAL_FOLDER_MODEL and not folder:
        return {"output_text": "", "error": _NO_MODEL_FOLDER, "duration_seconds": 0.0}
    return _run_transcription(cfg, case, use_gpu, folder, lambda: background_jobs.is_cancel_requested(JOB_ID))


def check_config(stage: str, cfg) -> dict:
    if not isinstance(cfg, dict):
        raise InvalidInputError("Each config needs an engine.")
    engine, model = cfg.get("engine"), cfg.get("model")
    if model is not None and not isinstance(model, str):
        raise InvalidInputError("model must be text.")
    if cfg.get("options") and stage == "ocr":
        raise InvalidInputError("Transcription options apply to transcription runs only.")
    if cfg.get("transcribe") is not None and stage != "translation":
        raise InvalidInputError("Only a translation run can translate a recogniser's output.")
    if cfg.get("translate") is not None and not vision.is_vision_arm(stage, cfg):
        raise InvalidInputError("Only a vision arm can translate what it reads.")
    if stage == "translation":
        entry = next((e for e in translate_service.list_engines() if e["name"] == engine), None)
        if entry is None:
            raise InvalidInputError("Unknown translate engine.")
        engine = entry["name"]
        model = vision.checked_model(entry, engine, model)
        checked = {"engine": engine, "model": model}
        if cfg.get("options") is not None:
            checked["options"] = scene_service.resolve_options(engine, model, cfg["options"])
        if cfg.get("transcribe") is not None:
            if cfg.get("options") is not None:
                raise InvalidInputError("A recogniser arm can't also take batch and context options yet.")
            checked["transcribe"] = check_config("transcription", cfg["transcribe"])
        return checked
    if stage == "transcription":
        options = transcribe_options_service.resolve_lab_options(cfg.get("options"))
        if engine in LAB_BACKENDS:
            return {"engine": engine, "model": None, "options": options}
        size = model or engine
        if size == benchmark.LOCAL_FOLDER_MODEL:
            if not _offline_model_folder():
                raise InvalidInputError(_NO_MODEL_FOLDER)
        elif size not in whisper_models.WHISPER_MODELS:
            raise InvalidInputError("Unknown Whisper size.")
        return {"engine": "whisper", "model": size, "options": options}
    if engine in vision.VISION_ENGINES:
        return vision.checked_vision_config(engine, model, cfg.get("translate"))
    if engine not in OCR_BACKENDS:
        raise InvalidInputError("Unknown OCR backend.")
    return {"engine": engine, "model": None}


def _check_selection(stage, configs, tier, set_name, case_ids):
    if stage not in STAGES:
        raise InvalidInputError("Unknown stage.")
    if tier is not None:
        _check_tier(tier)
    if not isinstance(configs, list) or not configs:
        raise InvalidInputError("Pick at least one engine to run.")
    if len(configs) > MAX_CONFIGS:
        raise InvalidInputError(f"At most {MAX_CONFIGS} engines at once.")
    checked = [check_config(stage, c) for c in configs]
    # Two arms of one model are a real comparison when their options differ.
    # An option the recogniser does not read cannot make two arms different.
    keys = [(c["engine"], c["model"], json.dumps(
        {k: v for k, v in (c.get("options") or {}).items() if k not in ignored_options(c["engine"])}, sort_keys=True),
        end_to_end.arm_key(c.get("transcribe")), json.dumps(c.get("translate"))) for c in checked]
    if len(set(keys)) != len(keys):
        raise InvalidInputError("The same engine and model is picked twice.")
    cases = _select_cases(stage, tier, set_name, case_ids, scorable_only=True)
    if not cases:
        raise UnsupportedOperationError("No benchmark cases match that selection.")
    if stage == "transcription":
        for cfg in checked:
            end_to_end.require_recogniser_fits(cfg, cases)
    end_to_end.require_audio_for([c["transcribe"] for c in checked if c.get("transcribe")], cases)
    vision.require_translation_references(stage, checked, cases)
    return checked, cases


def _estimate_config(cfg: dict, cases: list):
    """Pre-run cost of one translation config over these cases (the same
    chars-per-token heuristic as a normal translation estimate), or 0.0."""
    if not _cap_applies(cfg["engine"]):
        return 0.0
    engine_cls = engine_registry.ENGINES[cfg["engine"]]
    probe = type("Probe", (), {})()
    setattr(probe, "name", getattr(engine_cls, "name", cfg["engine"]))
    setattr(probe, "model", cfg["model"] or engine_registry.effective_default_model(cfg["engine"]))
    setattr(probe, "free_tier", False)
    if cfg.get("options") is not None:
        return scene_service.estimate_cost(probe, cfg["options"], cases)
    return scene_service.estimate_per_case_cost(probe, cases)


def estimate(stage: str, configs: list, tier: str | None = None, set_name: str | None = None,
             case_ids: list | None = None) -> dict:
    """What a run would cost before it starts, and whether the monthly cap
    allows it. Local stages (transcription/OCR) and free engines cost $0."""
    checked, cases = _check_selection(stage, configs, tier, set_name, case_ids)
    per_config = []
    for cfg in checked:
        cost = _estimate_config(cfg, cases) if stage == "translation" else vision.estimate_cost(
            stage, cfg, cases, _estimate_config)
        per_config.append({**cfg, "estimated_cost_usd": round(cost, 6),
                           "estimate_uncertain": stage == "translation" and scene_service.is_reasoning_arm(cfg),
                           "cap_applies": vision.paid_arm(stage, cfg) and vision.arm_capped(cfg, _cap_applies)})
    total = sum(p["estimated_cost_usd"] for p in per_config)
    monthly_cap = settings_service.get_monthly_cap_usd()
    spend = db.get_month_spend()
    any_capped = any(p["cap_applies"] for p in per_config)
    cap, refusal = pricing.resolve_cost_cap(None, monthly_cap, spend) if any_capped else (None, None)
    return {
        "stage": stage, "case_count": len(cases), "configs": per_config,
        "estimated_cost_usd": round(total, 6), "monthly_cap_usd": monthly_cap,
        # With no cap the panel shows the month's real spend, not the since-reset count.
        "month_spend_usd": round(spend if monthly_cap > 0 else db.get_month_spend(since_reset=False), 6),
        "remaining_usd": None if cap is None else round(cap, 6),
        "monthly_refusal": refusal,
        "estimate_above_cap": bool(cap is not None and total > cap),
        "estimate_uncertain": any(p["estimate_uncertain"] and p["cap_applies"] for p in per_config),
    }


def start_run(stage: str, configs: list, tier: str | None = None, set_name: str | None = None,
              case_ids: list | None = None, label: str = "", prompt_version: str = "",
              use_gpu: bool | None = None, max_cost_usd: float | None = None, judge: dict | None = None) -> dict:
    """Starts a background run: one benchmark_sessions row per config
    (several configs = one Model Arena group). Refused when the monthly cap
    is used up or the estimate is over what's left of it. max_cost_usd is an
    optional cap on the run's total real spend across all its configs (0 =
    free engines only); the run stops with stopped_cap once it is reached."""
    label = _clean_text(label, "label", required=False, max_len=MAX_LABEL_CHARS)
    prompt_version = _clean_text(prompt_version, "prompt_version", required=False, max_len=60)
    est = estimate(stage, configs, tier, set_name, case_ids)
    if est["monthly_refusal"]:
        raise UnsupportedOperationError(est["monthly_refusal"])
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            f"This run is estimated at ${est['estimated_cost_usd']:.4f}, more than the "
            f"${est['remaining_usd']:.4f} left of this month's cap.")
    checked, cases = _check_selection(stage, configs, tier, set_name, case_ids)
    engines = vision.resolve_keys(stage, checked)
    if _job_active():
        raise ConflictError("A benchmark run is already going.")
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
    use_gpu = bool(settings_service.get("use_gpu")) if use_gpu is None else bool(use_gpu)
    end_to_end.check_start(stage, checked, judge, use_gpu)
    arena_group = uuid.uuid4().hex[:12] if len(checked) > 1 else None
    case_filter = {"tier": tier, "set_name": set_name, "case_ids": sorted(case_ids) if case_ids else None}
    with benchmark_runs_service.starting:
        session_ids = [create_session(stage, cfg, cases, use_gpu, label=label, prompt_version=prompt_version,
                                      case_filter=case_filter, arena_group=arena_group) for cfg in checked]
        plan = list(zip(session_ids, checked, engines))
        started = job_launch.start_job(
            JOB_ID, _run_job, JOB_ID, stage, plan, [c["id"] for c in cases], use_gpu, max_cost_usd, judge,
            gpu_touching=vision.needs_gpu(stage, checked) or any(
                c.get("transcribe") or local.ollama_touches_local_gpu(c["engine"], c["model"])
                for c in checked),
            description="Benchmark run", line_fields=())
    if not started:
        # Nothing ran: leave no rows behind.
        db.delete_benchmark_sessions(session_ids)
        if job_launch.exclusive_active() or job_launch.maintenance_active():
            raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
        raise ConflictError("A benchmark run is already going.")
    return {"job_id": JOB_ID, "session_ids": session_ids, "arena_group": arena_group,
            "estimated_cost_usd": est["estimated_cost_usd"]}


def run_context(stage: str, cfg: dict, cases: list, use_gpu) -> dict:
    """What a run records about how it was set up and scored (benchmark_sessions.context_settings)."""
    context = {"use_gpu": use_gpu} if stage != "translation" else {}
    if stage == "transcription":
        # Runs without this key were scored on punctuation and width.
        context["text_normaliser"] = 2
    if stage != "translation" and any(c.get("source_language") == "zh" for c in cases):
        # Runs without this key were scored script-sensitively.
        context["script_normaliser"] = script_normaliser(stage)
    context.update(app_version=app_version(), model_identity=model_identity(stage, cfg))
    if stage == "translation" and cfg.get("options") is not None:
        context.update(scene_service.run_context(cfg))
    if cfg.get("transcribe"):
        context.update(end_to_end.run_context(cfg["transcribe"], use_gpu))
    if cfg.get("translate"):
        context["translate_model_identity"] = model_identity("translation", cfg["translate"])
    if stage == "transcription":
        context["transcribe_options"] = cfg["options"]
        # What the arm changed from the app's defaults: what tells two arms of one model apart in a list.
        defaults = transcribe_options_service.lab_option_defaults()
        context["transcribe_changes"] = {k: v for k, v in cfg["options"].items() if v != defaults[k]}
        context["transcribe_options_ignored"] = ignored_options(cfg["engine"])
    return context


def create_session(stage: str, cfg: dict, cases: list, use_gpu, label: str = "", prompt_version: str = "",
                   case_filter: dict | None = None, arena_group: str | None = None, extra_context: dict | None = None) -> int:
    """One queued benchmark_sessions row for one checked config over these cases."""
    model = (benchmark.LOCAL_FOLDER_LABEL if cfg["model"] == benchmark.LOCAL_FOLDER_MODEL else cfg["model"]) \
        or (engine_registry.effective_default_model(cfg["engine"])
            if vision.paid_arm(stage, cfg) else None)
    return db.create_benchmark_session({
        "label": label, "stage": stage, "engine": cfg["engine"], "model": model,
        "prompt_version": prompt_version,
        "context_settings": json.dumps({**run_context(stage, cfg, cases, use_gpu), **(extra_context or {})}),
        "case_filter": json.dumps(case_filter or {}), "arena_group": arena_group, "status": "queued",
        "case_count": len(cases)})


def _now() -> str:
    return clock.utcnow().isoformat()


def _peak_vram_mb():
    try:
        import asr_benchmark
        return asr_benchmark.read_peak_vram_mb()
    except Exception:
        return None


def _reset_vram():
    try:
        import asr_benchmark
        asr_benchmark.reset_vram_counter()
    except Exception:
        pass


def _run_job(job_id, stage, plan, case_ids, use_gpu, job_cap=None, judge=None):
    note = "The run stopped unexpectedly."
    try:
        _run_plan(job_id, stage, plan, case_ids, use_gpu, job_cap)
        if judge:
            from services import benchmark_judge_service
            benchmark_judge_service.run_pass(job_id, plan, judge)
    except Exception as exc:
        note = f"The run stopped: {redact(exc) or type(exc).__name__}"
        raise
    finally:
        if stage == "transcription" or any(cfg.get("transcribe") for _sid, cfg, _key in plan):
            # This job runs in the API process: Qwen3-ASR, anime-whisper and the aligner would
            # otherwise stay on the GPU and break the next title's run.
            whisper_models.release_gpu_models()
        # A crash mid-run must not leave a run looking "running" forever.
        for sid, _cfg, _key in plan:
            s = db.get_benchmark_session(sid)
            if s and s.get("status") in ("queued", "running"):
                db.update_benchmark_session(sid, status="failed", finished_at=_now(), note=note)


def _run_plan(job_id, stage, plan, case_ids, use_gpu, job_cap=None, progress=None):
    """progress(fraction, message), when given, takes this plan's progress instead of the job's
    own bar, and the job's result is left to the caller (the sweep runs one plan per pass)."""
    report = progress or (lambda frac, message: background_jobs.update_progress(job_id, frac, message))
    cases = {c["id"]: c for c in db.list_benchmark_cases(stage)}
    ordered = [cases[i] for i in case_ids if i in cases]
    total_steps = max(1, len(ordered) * len(plan))
    step = 0
    # Fixed for the run: a Settings change mid-run would mix two models in one session.
    folder = _offline_model_folder() if stage in ("transcription", "translation") else None
    run_spent = 0.0     # real spend across every config in this run
    for session_id, cfg, api_key in plan:
        db.update_benchmark_session(session_id, status="running")
        engine, cap, capped, scenes = None, None, False, None
        if vision.paid_arm(stage, cfg):
            try:
                engine = engine_registry.get_engine(
                    cfg["engine"], api_key, cfg["model"],
                    free_tier=cfg["engine"] == "gemini" and bool(settings_service.get("gemini_free_tier")),
                    base_url=settings_service.ollama_url()
                    if cfg["engine"] == "ollama" else None)
            except Exception as exc:
                db.update_benchmark_session(session_id, status="failed", note=redact(exc, api_key),
                                            finished_at=_now())
                step += len(ordered)
                continue
            if vision.arm_capped(cfg, _cap_applies):
                monthly = settings_service.get_monthly_cap_usd()
                # resolve_cost_cap reads 0 as "no cap", so a job cap that is
                # used up (or 0) is handled here, never passed through.
                job_left = None if job_cap is None else max(float(job_cap) - run_spent, 0.0)
                if job_left is not None and job_left <= 0:
                    cap, refusal = None, "This run's spending limit is reached."
                else:
                    cap, refusal = pricing.resolve_cost_cap(
                        job_left, monthly, db.get_month_spend() if monthly else 0.0)
                if refusal:
                    db.update_benchmark_session(session_id, status="stopped_cap", note=refusal,
                                                finished_at=_now())
                    step += len(ordered)
                    continue
        if engine is not None and cfg.get("options") is not None:
            scenes = scene_service.SceneRunner(engine, cfg, ordered, lambda: background_jobs.is_cancel_requested(job_id), api_key)
        _reset_vram()
        spent, scores, passed, errors, durations, unreadable = 0.0, [], 0, 0, [], 0
        status = "done"
        streak, stop_note = FailureStreak(), None
        # Per engine: one engine's waits must not use up the next one's budget.
        budget_token = _rate_limit_budget_var.set(RateLimitWaitBudget())
        for case in ordered:
            if background_jobs.is_cancel_requested(job_id):
                status = "cancelled"
                break
            if cap is not None and spent >= cap:
                status, capped = "stopped_cap", True
                break
            report(
                step / total_steps,
                f"{cfg['engine']}{' ' + cfg['model'] if cfg.get('model') else ''}: "
                f"{case.get('label') or case['id']}")
            if stage == "translation":
                # Scope only for the cancel check: it lets call_with_backoff
                # stop at its next retry instead of sleeping out a cancelled run.
                with llm_tasks.bounded_llm_calls(
                        job_id, lambda: background_jobs.is_cancel_requested(job_id)):
                    if scenes:
                        r = scenes.result(case, None if cap is None else max(cap - scenes.total, 0.0))
                    elif cfg.get("transcribe"):
                        r = end_to_end.translate_recognised(engine, cfg["transcribe"], case, api_key, use_gpu, folder, job_id)
                    else:
                        r = _run_translation(engine, case, api_key)
                stop_note = r.pop("stop_reason", "")
                # A clip the recogniser failed on says nothing about whether the engine is answering.
                heard_nothing = r.pop("recognition_failed", False)
                if not stop_note and streak.record(failed=bool(r.get("error")) and not heard_nothing,
                                                   succeeded=not r.get("error")):
                    stop_note = paid_calls.failure_stop_reason(streak.count)
                logged = r.pop("usage_logged", False)
                if (r["cost_usd"] or r["usage"]) and not logged:
                    db.log_usage(None, cfg["engine"], getattr(engine, "model", cfg["model"]) or "",
                                 "benchmark", r["usage"].get("input_tokens", 0) or 0,
                                 r["usage"].get("output_tokens", 0) or 0, r["cost_usd"],
                                 r["usage"].get("cache_read_tokens", 0) or 0)
                # A scene is paid for when it is translated, so its real total, not the shares of the
                # cases consumed so far, is what the cap (and the next config's budget) sees.
                spent = scenes.total if scenes else spent + r["cost_usd"]
                # After the usage log: a request that finished and was billed
                # while a cancel was pending still counts toward the cap.
                if background_jobs.is_cancel_requested(job_id):
                    # The interrupted case would be saved as an engine error.
                    status = "cancelled"
                    break
            elif vision.is_vision_arm(stage, cfg):
                r = vision.run_case(engine, cfg, case, api_key, None if cap is None else max(cap - spent, 0.0),
                                    job_id, _estimate_config)
                spent += r["cost_usd"]
                if background_jobs.is_cancel_requested(job_id):
                    status = "cancelled"
                    break
                if r.pop("over_budget", False):
                    status, capped = "stopped_cap", True
                    break
                if streak.record(failed=bool(r.get("error")) and not r.get("unreadable"), succeeded=not r.get("error")):
                    stop_note = paid_calls.failure_stop_reason(streak.count)
            else:
                r = _run_file_case(stage, cfg, case, use_gpu, folder)
                if background_jobs.is_cancel_requested(job_id):
                    # The interrupted case would be saved as an engine error.
                    status = "cancelled"
                    break
            score, metric, scorer = vision.score(stage, cfg, r, case)
            if r.get("error") and score is not None:
                # A referenced case that errored is a fail scored 0, so an
                # engine that errors on most cases can't rank above one that
                # answers them all.
                score = 0.0
            r["score"], r["metric"], r["scorer"] = score, metric, scorer
            r["passed"] = None if score is None else score >= pass_threshold(metric)
            db.save_benchmark_result(session_id, case, r)
            unreadable += bool(r.get("unreadable"))
            errors += bool(r.get("error")) and not r.get("unreadable")
            if score is not None:
                scores.append(score)
                passed += 1 if r["passed"] else 0
            durations.append(r.get("duration_seconds") or 0.0)
            step += 1
            if stop_note:
                # This engine is not answering: its remaining cases would only
                # repeat the failure (or the bill). The next config still runs.
                status = "failed"
                break
        _rate_limit_budget_var.reset(budget_token)
        run_spent += spent
        db.update_benchmark_session(
            session_id, status=status, scored_count=len(scores), passed_count=passed,
            error_count=errors,
            aggregate_score=(sum(scores) / len(scores)) if scores else None,
            avg_latency_seconds=(sum(durations) / len(durations)) if durations else None,
            total_cost_usd=round(spent, 6), peak_vram_mb=_peak_vram_mb(),
            note="Stopped at the spending cap." if capped else stop_note or vision.unreadable_note(unreadable),
            finished_at=_now())
        if status == "cancelled":
            for sid, _cfg, _k in plan[plan.index((session_id, cfg, api_key)) + 1:]:
                db.update_benchmark_session(sid, status="cancelled", finished_at=_now())
            break
    if progress is None:
        background_jobs.set_result(job_id, {"session_ids": [p[0] for p in plan]})
        background_jobs.update_progress(job_id, 1.0, "Benchmark finished.")


# ---------------------------------------------------------------------------
# Reading runs back (the shaping lives in benchmark_runs_service)
# ---------------------------------------------------------------------------

def list_runs(stage: str | None = None, limit: int = 50) -> dict:
    if stage is not None and stage not in STAGES:
        raise InvalidInputError("Unknown stage.")
    close_stale_runs()
    limit = max(1, min(int(limit or 50), 200))
    return {"runs": [_session_out(s) for s in db.list_benchmark_sessions(limit, stage)]}


def delete_run(run_id: int) -> dict:
    """Removes a run and its results. Refused while a benchmark job is going
    (its judge pass writes beside the run's own rows)."""
    close_stale_runs()
    _refuse_while_running()
    return benchmark_runs_service.delete_run(run_id)
