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
returned; errors are passed through translate_engines.redact_secrets before
they are stored.

Not built here: scoped translation-memory saves,
the auto-derived Translation Profile and a COMET scorer.
"""
import functools
import json
import time
import uuid

import background_jobs
import benchmark
import db
import translate_engines
from core import SOURCE_LANGUAGES
from services import settings_service, translate_service
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    UnsupportedOperationError,
)

JOB_ID = "benchmark_lab"
TIERS = ("public", "application", "regression")
STAGES = ("translation", "transcription", "ocr")
REGRESSION_SET = "regressions"
# A result at or above this counts as a pass (the per-example pass/fail).
PASS_THRESHOLD = 0.8
# chrF scores a sentence far lower than difflib does for the same acceptable
# paraphrase (a close rewording lands near 0.5-0.6, a different meaning below
# 0.3 on hand-checked en/zh pairs), so 0.8 would fail nearly everything.
CHRF_PASS_THRESHOLD = 0.5
MAX_CONFIGS = 4
MAX_TEXT_CHARS = 4000
MAX_IMPORT_CASES = 500
MAX_IMPORT_BYTES = 2_000_000
MAX_LABEL_CHARS = 120
MAX_SET_NAME_CHARS = 60
OCR_BACKENDS = ("tesseract", "paddle", "manga_ocr", "paddle_vl_manga")
# Engines whose spend counts toward the monthly cap (as translate_run_service).
_CAP_ENGINES = ("claude", "deepseek", "gemini", "openai")
# Languages whose transcripts are scored per character (no word spaces).
_CHARACTER_LANGUAGES = SOURCE_LANGUAGES


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _edit_distance(a, b) -> int:
    """Levenshtein distance between two sequences (strings or word lists)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


# Scoring is O(n*m) in pure Python: an output far longer than its reference
# is cut to a bound (its extra length already counts fully as errors).
_MAX_SCORED_OUTPUT_FACTOR = 2


def error_rate(actual: str, reference: str, unit: str = "char") -> float:
    """Built-in CER (unit "char", whitespace ignored) or WER (unit "word"):
    edits needed to turn the output into the reference, over the reference
    length. Can exceed 1.0 when the output is much longer."""
    if unit == "word":
        a, r = (actual or "").split(), (reference or "").split()
    else:
        a, r = "".join((actual or "").split()), "".join((reference or "").split())
    if not r:
        return 0.0 if not a else 1.0
    r = r[:MAX_TEXT_CHARS]
    limit = _MAX_SCORED_OUTPUT_FACTOR * len(r) + 10
    extra = max(0, len(a) - limit)
    return (_edit_distance(a[:limit], r) + extra) / len(r)


@functools.lru_cache(maxsize=None)
def _jiwer_transform(jiwer, unit: str, keep_punctuation: bool = False):
    """jiwer's usual normalisation, applied the same way to both texts:
    lower-case, punctuation (unless kept, as for OCR) and extra whitespace
    removed, then split into words (WER) or characters with all whitespace
    dropped (CER). Cached: jiwer 3.x's RemovePunctuation scans all of Unicode
    when it is built."""
    steps = [jiwer.ToLowerCase()]
    if not keep_punctuation:
        steps.append(jiwer.RemovePunctuation())
    if unit == "word":
        steps += [jiwer.RemoveMultipleSpaces(), jiwer.Strip(), jiwer.ReduceToListOfListOfWords()]
    else:
        # Every Unicode space (U+3000 in CJK text too), not only ASCII ones
        # as jiwer.RemoveWhiteSpace does.
        steps += [jiwer.SubstituteRegexes({r"\s+": ""}), jiwer.ReduceToListOfListOfChars()]
    return jiwer.Compose(steps)


def _jiwer_error_rate(actual: str, reference: str, unit: str, keep_punctuation: bool = False):
    """CER/WER from jiwer, or None when jiwer isn't installed or the
    reference is empty after normalisation (the caller falls back)."""
    try:
        import jiwer
    except ImportError:
        return None
    transform = _jiwer_transform(jiwer, unit, keep_punctuation)
    r = [t for sentence in transform(reference or "") for t in sentence][:MAX_TEXT_CHARS]
    if not r:
        return None
    a = [t for sentence in transform(actual or "") for t in sentence]
    # Same length bound as the built-in scorer: output past the bound counts
    # fully as insertions and isn't aligned. Tokens hold no whitespace, so
    # joining them with spaces makes jiwer's word alignment a token alignment.
    limit = _MAX_SCORED_OUTPUT_FACTOR * len(r) + 10
    extra = max(0, len(a) - limit)
    out = jiwer.process_words(" ".join(r), " ".join(a[:limit]))
    return (out.substitutions + out.deletions + out.insertions + extra) / len(r)


def pass_threshold(metric: str) -> float:
    return CHRF_PASS_THRESHOLD if metric == "chrf" else PASS_THRESHOLD


def score_output(stage: str, output: str, reference: str, source_language: str = "zh"):
    """(score 0.0-1.0 or None when there is no reference, metric name,
    scorer): scorer is "jiwer" or "builtin" for CER/WER, "sacrebleu" or
    "builtin" for translation similarity, None when nothing was scored."""
    if stage == "translation":
        if not reference:
            return None, "similarity", None
        score, metric = benchmark.translation_similarity(output, reference)
        return score, metric, "sacrebleu" if metric == "chrf" else "builtin"
    unit = "char" if stage == "ocr" or source_language in _CHARACTER_LANGUAGES else "word"
    metric = "cer" if unit == "char" else "wer"
    if not reference:
        return None, metric, None
    # User decision (2026-09-30): OCR keeps punctuation in its CER.
    rate, scorer = _jiwer_error_rate(output, reference, unit,
                                     keep_punctuation=stage == "ocr"), "jiwer"
    if rate is None:
        rate, scorer = error_rate(output, reference, unit), "builtin"
    return max(0.0, 1.0 - rate), metric, scorer


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


def _case_out(c: dict) -> dict:
    """A case for the API: never the stored input file name or path."""
    return {
        "id": c["id"], "label": c.get("label") or "", "stage": c.get("stage"),
        "tier": c.get("tier") or "application", "set_name": c.get("set_name") or "",
        "source_language": c.get("source_language") or "zh",
        "source_text": c.get("source_text"), "reference_text": c.get("reference_text"),
        "has_reference": bool(c.get("reference_text")),
        "has_input_file": bool(c.get("input_filename")),
        "origin_drama_id": c.get("origin_drama_id"), "origin_line_id": c.get("origin_line_id"),
        "created_at": c.get("created_at"),
    }


def _select_cases(stage: str, tier: str = None, set_name: str = None, case_ids=None) -> list:
    cases = db.list_benchmark_cases(stage)
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


def list_cases(stage: str = None, tier: str = None, set_name: str = None) -> dict:
    if stage is not None and stage not in STAGES:
        raise InvalidInputError("Unknown stage.")
    if tier is not None:
        _check_tier(tier)
    cases = db.list_benchmark_cases(stage)
    if tier:
        cases = [c for c in cases if (c.get("tier") or "application") == tier]
    if set_name:
        cases = [c for c in cases if (c.get("set_name") or "") == set_name]
    return {"cases": [_case_out(c) for c in cases]}


def list_sets() -> dict:
    """One row per (stage, tier, set name) with its case count."""
    groups = {}
    for c in db.list_benchmark_cases():
        key = (c.get("stage"), c.get("tier") or "application", c.get("set_name") or "")
        g = groups.setdefault(key, {"stage": key[0], "tier": key[1], "set_name": key[2],
                                    "case_count": 0, "with_reference": 0})
        g["case_count"] += 1
        g["with_reference"] += 1 if c.get("reference_text") else 0
    return {"sets": sorted(groups.values(), key=lambda g: (g["stage"] or "", g["tier"], g["set_name"]))}


def create_case(label: str, source_text: str, reference_text: str = None,
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
    return _case_out(db.get_benchmark_case(case_id))


def _job_active() -> bool:
    st = background_jobs.get_status(JOB_ID)
    return bool(st and st.get("status") in ("running", "queued"))


def _refuse_while_running():
    if _job_active():
        raise ConflictError("A benchmark run is going; wait for it to finish.")


def delete_case(case_id: int) -> dict:
    _refuse_while_running()
    if not db.get_benchmark_case(case_id):
        raise NotFoundError("Benchmark case not found.")
    db.delete_benchmark_case(case_id)
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
    lang = drama.get("source_language") if drama.get("source_language") in SOURCE_LANGUAGES else "zh"
    existing = db.find_benchmark_case(REGRESSION_SET, source, origin_line_id=line_id)
    if existing:
        # Updated in place, so earlier runs' results stay tied to this case.
        _refuse_while_running()
        db.update_benchmark_case_texts(existing["id"], source, fixed)
        return {"case": _case_out(db.get_benchmark_case(existing["id"])), "replaced": True}
    title = drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}"
    case_id = db.create_benchmark_lab_case(
        f"{title[:80]} · line {line.get('idx', 0) + 1}", "translation", lang, source, fixed,
        "regression", REGRESSION_SET, content_type=drama.get("content_mode") or "audio_drama",
        origin_drama_id=drama_id, origin_line_id=line_id)
    return {"case": _case_out(db.get_benchmark_case(case_id)), "replaced": bool(existing)}


# ---------------------------------------------------------------------------
# Options, estimate, run
# ---------------------------------------------------------------------------

def get_options() -> dict:
    import core
    return {
        "stages": list(STAGES), "tiers": list(TIERS), "source_languages": list(SOURCE_LANGUAGES),
        "translation_engines": translate_service.list_engines(),
        "whisper_sizes": list(core.WHISPER_MODELS.keys()),
        "ocr_backends": list(OCR_BACKENDS),
        "pass_threshold": PASS_THRESHOLD, "chrf_pass_threshold": CHRF_PASS_THRESHOLD,
        "max_configs": MAX_CONFIGS,
    }


def _cap_applies(engine: str) -> bool:
    return engine in _CAP_ENGINES and not (
        engine == "gemini" and settings_service.get_gemini_free_tier())


def check_config(stage: str, cfg) -> dict:
    if not isinstance(cfg, dict):
        raise InvalidInputError("Each config needs an engine.")
    engine, model = cfg.get("engine"), cfg.get("model")
    if model is not None and not isinstance(model, str):
        raise InvalidInputError("model must be text.")
    if stage == "translation":
        entry = next((e for e in translate_service.list_engines() if e["name"] == engine), None)
        if entry is None:
            raise InvalidInputError("Unknown translate engine.")
        if model is not None:
            if engine == "ollama":
                # Only ever sent to the local Ollama server; shape-checked.
                if not model or len(model) > 100 or any(ch.isspace() for ch in model) \
                        or ".." in model or model.startswith("/"):
                    raise InvalidInputError("That model isn't offered for this engine.")
            elif entry["models"] is None and model == default_model(engine):
                model = None   # an engine without a model picker: its built-in model
            elif entry["models"] is None or model not in entry["models"]:
                raise InvalidInputError("That model isn't offered for this engine.")
        return {"engine": engine, "model": model}
    if stage == "transcription":
        import core
        size = model or engine
        if size not in core.WHISPER_MODELS:
            raise InvalidInputError("Unknown Whisper size.")
        return {"engine": "whisper", "model": size}
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
    keys = [(c["engine"], c["model"]) for c in checked]
    if len(set(keys)) != len(keys):
        raise InvalidInputError("The same engine and model is picked twice.")
    cases = _select_cases(stage, tier, set_name, case_ids)
    if not cases:
        raise UnsupportedOperationError("No benchmark cases match that selection.")
    return checked, cases


def _estimate_config(cfg: dict, cases: list):
    """Pre-run cost of one translation config over these cases (the same
    chars-per-token heuristic as a normal translation estimate), or 0.0."""
    if not _cap_applies(cfg["engine"]):
        return 0.0
    engine_cls = translate_engines.ENGINES[cfg["engine"]]
    probe = type("Probe", (), {})()
    probe.name = getattr(engine_cls, "name", cfg["engine"])
    probe.model = cfg["model"] or default_model(cfg["engine"])
    probe.free_tier = False
    # One call per case, so the fixed instructions overhead is paid per case.
    return sum(translate_engines.estimate_translation_cost(probe, [c.get("source_text") or ""])
               for c in cases)


def default_model(engine: str):
    return translate_engines.effective_default_model(engine)


def estimate(stage: str, configs: list, tier: str = None, set_name: str = None,
             case_ids: list = None) -> dict:
    """What a run would cost before it starts, and whether the monthly cap
    allows it. Local stages (transcription/OCR) and free engines cost $0."""
    checked, cases = _check_selection(stage, configs, tier, set_name, case_ids)
    per_config = []
    for cfg in checked:
        cost = _estimate_config(cfg, cases) if stage == "translation" else 0.0
        per_config.append({**cfg, "estimated_cost_usd": round(cost, 6),
                           "cap_applies": stage == "translation" and _cap_applies(cfg["engine"])})
    total = sum(p["estimated_cost_usd"] for p in per_config)
    monthly_cap = settings_service.get_monthly_cap_usd()
    spend = db.get_month_spend()
    any_capped = any(p["cap_applies"] for p in per_config)
    cap, refusal = translate_engines.resolve_cost_cap(None, monthly_cap, spend) if any_capped else (None, None)
    return {
        "stage": stage, "case_count": len(cases), "configs": per_config,
        "estimated_cost_usd": round(total, 6), "monthly_cap_usd": monthly_cap,
        # With no cap the panel shows the month's real spend, not the since-reset count.
        "month_spend_usd": round(spend if monthly_cap > 0 else db.get_month_spend(since_reset=False), 6),
        "remaining_usd": None if cap is None else round(cap, 6),
        "monthly_refusal": refusal,
        "estimate_above_cap": bool(cap is not None and total > cap),
    }


def start_run(stage: str, configs: list, tier: str = None, set_name: str = None,
              case_ids: list = None, label: str = "", prompt_version: str = "",
              use_gpu: bool = None, max_cost_usd: float = None) -> dict:
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
    engines = []
    if stage == "translation":
        for cfg in checked:
            key = translate_service.resolve_api_key(cfg["engine"])
            if key is None and cfg["engine"] != "nllb":
                raise MissingKeyError(cfg['engine'])
            engines.append(key)
    if _job_active():
        raise ConflictError("A benchmark run is already going.")
    if background_jobs.exclusive_active() or background_jobs.maintenance_active():
        raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
    use_gpu = settings_service.get_use_gpu() if use_gpu is None else bool(use_gpu)
    arena_group = uuid.uuid4().hex[:12] if len(checked) > 1 else None
    case_filter = json.dumps({"tier": tier, "set_name": set_name,
                              "case_ids": sorted(case_ids) if case_ids else None})
    context = {"use_gpu": use_gpu} if stage != "translation" else {}
    session_ids = []
    for cfg in checked:
        session_ids.append(db.create_benchmark_session({
            "label": label, "stage": stage, "engine": cfg["engine"], "model": cfg["model"]
            or (default_model(cfg["engine"]) if stage == "translation" else None),
            "prompt_version": prompt_version, "context_settings": json.dumps(context),
            "case_filter": case_filter, "arena_group": arena_group, "status": "queued",
            "case_count": len(cases)}))
    plan = list(zip(session_ids, checked, engines or [None] * len(checked)))
    started = background_jobs.start_job(
        JOB_ID, _run_job, JOB_ID, stage, plan, [c["id"] for c in cases], use_gpu, max_cost_usd,
        gpu_touching=stage != "translation" or any(c["engine"] in ("ollama", "nllb") for c in checked),
        description="Benchmark run")
    if not started:
        # Nothing ran: leave no rows behind.
        db.delete_benchmark_sessions(session_ids)
        if background_jobs.exclusive_active() or background_jobs.maintenance_active():
            raise ConflictError("A library restore or cleanup is running; try again when it finishes.")
        raise ConflictError("A benchmark run is already going.")
    return {"job_id": JOB_ID, "session_ids": session_ids, "arena_group": arena_group,
            "estimated_cost_usd": est["estimated_cost_usd"]}


def _now() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat()


def redact(text, key=None):
    """Secrets (redact_secrets, plus the run's own key by value, since a key
    without a recognisable prefix would otherwise slip through) and absolute
    paths / the OS user name (diagnostics.redact_for_support, via
    jobs_service's never-raising wrapper): errors are shown to remote admins."""
    if not text:
        return text
    text = str(text)
    if key and len(key) >= 8:
        text = text.replace(key, "[redacted]")
    from services import jobs_service
    return jobs_service.redact_text(text)


def _translation_context(case: dict) -> dict:
    """The case's own source language, where every engine reads it (MT
    engines from source_language, LLM prompts from drama_meta); an empty
    context would make every engine treat Japanese/Korean as Chinese."""
    lang = case.get("source_language") if case.get("source_language") in SOURCE_LANGUAGES else "zh"
    return {"source_language": lang, "target_language": "en",
            "drama_meta": {"source_language": lang}}


def _run_translation(engine, case: dict, key: str = None) -> dict:
    started = time.monotonic()
    usage = None
    try:
        # Same retry as a normal translation: a transient 429/5xx shouldn't
        # score 0 and skew the Arena.
        ctx = _translation_context(case)
        out = translate_engines.call_with_backoff(
            lambda: engine.translate_batch([case.get("source_text") or ""], ctx))
        output_text = out[0] if out else ""
        error = None
        usage = getattr(engine, "last_usage", None)
    except Exception as exc:
        output_text, error = "", redact(exc, key)
    cost = 0.0
    if usage:
        cost = translate_engines.estimate_cost_for_engine(
            engine, usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0,
            usage.get("cache_read_tokens", 0) or 0, usage.get("cache_write_tokens", 0) or 0)
    return {"output_text": output_text, "error": error, "cost_usd": cost, "usage": usage or {},
            "duration_seconds": time.monotonic() - started}


def _run_file_case(stage: str, cfg: dict, case: dict, use_gpu: bool) -> dict:
    import os
    if not case.get("input_filename"):
        return {"output_text": "", "error": "This case has no input file.", "duration_seconds": 0.0}
    prepared = dict(case, input_path=os.path.join(db.BENCHMARK_DIR, case["input_filename"]))
    if stage == "transcription":
        r = benchmark.run_transcription_case(prepared, whisper_size=cfg["model"], use_gpu=use_gpu)
    else:
        r = benchmark.run_ocr_case(prepared, backend=cfg["engine"])
    r["error"] = redact(r.get("error"))
    return r


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


def _run_job(job_id, stage, plan, case_ids, use_gpu, job_cap=None):
    try:
        _run_plan(job_id, stage, plan, case_ids, use_gpu, job_cap)
    finally:
        # A crash mid-run must not leave a run looking "running" forever.
        for sid, _cfg, _key in plan:
            s = db.get_benchmark_session(sid)
            if s and s.get("status") in ("queued", "running"):
                db.update_benchmark_session(sid, status="failed", finished_at=_now(),
                                            note="The run stopped unexpectedly.")


def _run_plan(job_id, stage, plan, case_ids, use_gpu, job_cap=None):
    cases = {c["id"]: c for c in db.list_benchmark_cases(stage)}
    ordered = [cases[i] for i in case_ids if i in cases]
    total_steps = max(1, len(ordered) * len(plan))
    step = 0
    run_spent = 0.0     # real spend across every config in this run
    for session_id, cfg, api_key in plan:
        db.update_benchmark_session(session_id, status="running")
        engine, cap, capped = None, None, False
        if stage == "translation":
            try:
                engine = translate_engines.get_engine(
                    cfg["engine"], api_key, cfg["model"],
                    free_tier=cfg["engine"] == "gemini" and settings_service.get_gemini_free_tier(),
                    base_url=(settings_service.resolve_key("ollama_url") or None)
                    if cfg["engine"] == "ollama" else None)
            except Exception as exc:
                db.update_benchmark_session(session_id, status="failed", note=redact(exc, api_key),
                                            finished_at=_now())
                step += len(ordered)
                continue
            if _cap_applies(cfg["engine"]):
                monthly = settings_service.get_monthly_cap_usd()
                # resolve_cost_cap reads 0 as "no cap", so a job cap that is
                # used up (or 0) is handled here, never passed through.
                job_left = None if job_cap is None else max(float(job_cap) - run_spent, 0.0)
                if job_left is not None and job_left <= 0:
                    cap, refusal = None, "This run's spending limit is reached."
                else:
                    cap, refusal = translate_engines.resolve_cost_cap(
                        job_left, monthly, db.get_month_spend() if monthly else 0.0)
                if refusal:
                    db.update_benchmark_session(session_id, status="stopped_cap", note=refusal,
                                                finished_at=_now())
                    step += len(ordered)
                    continue
        _reset_vram()
        spent, scores, passed, errors, durations = 0.0, [], 0, 0, []
        status = "done"
        for case in ordered:
            if background_jobs.is_cancel_requested(job_id):
                status = "cancelled"
                break
            if cap is not None and spent >= cap:
                status, capped = "stopped_cap", True
                break
            background_jobs.update_progress(
                job_id, step / total_steps,
                f"{cfg['engine']}{' ' + cfg['model'] if cfg.get('model') else ''}: "
                f"{case.get('label') or case['id']}")
            if stage == "translation":
                r = _run_translation(engine, case, api_key)
                if r["cost_usd"] or r["usage"]:
                    db.log_usage(None, cfg["engine"], getattr(engine, "model", cfg["model"]) or "",
                                 "benchmark", r["usage"].get("input_tokens", 0) or 0,
                                 r["usage"].get("output_tokens", 0) or 0, r["cost_usd"],
                                 r["usage"].get("cache_read_tokens", 0) or 0)
                spent += r["cost_usd"]
            else:
                r = _run_file_case(stage, cfg, case, use_gpu)
            score, metric, scorer = score_output(stage, r.get("output_text") or "",
                                                 case.get("reference_text"),
                                                 case.get("source_language"))
            if r.get("error") and score is not None:
                # A referenced case that errored is a fail scored 0, so an
                # engine that errors on most cases can't rank above one that
                # answers them all.
                score = 0.0
            r["score"], r["metric"], r["scorer"] = score, metric, scorer
            r["passed"] = None if score is None else score >= pass_threshold(metric)
            db.save_benchmark_result(session_id, case, r)
            if r.get("error"):
                errors += 1
            if score is not None:
                scores.append(score)
                passed += 1 if r["passed"] else 0
            durations.append(r.get("duration_seconds") or 0.0)
            step += 1
        run_spent += spent
        db.update_benchmark_session(
            session_id, status=status, scored_count=len(scores), passed_count=passed,
            error_count=errors,
            aggregate_score=(sum(scores) / len(scores)) if scores else None,
            avg_latency_seconds=(sum(durations) / len(durations)) if durations else None,
            total_cost_usd=round(spent, 6), peak_vram_mb=_peak_vram_mb(),
            note="Stopped at the spending cap." if capped else None, finished_at=_now())
        if status == "cancelled":
            for sid, _cfg, _k in plan[plan.index((session_id, cfg, api_key)) + 1:]:
                db.update_benchmark_session(sid, status="cancelled", finished_at=_now())
            break
    background_jobs.set_result(job_id, {"session_ids": [p[0] for p in plan]})
    background_jobs.update_progress(job_id, 1.0, "Benchmark finished.")


# ---------------------------------------------------------------------------
# Reading runs back
# ---------------------------------------------------------------------------

_SESSION_FIELDS = ("id", "label", "stage", "engine", "model", "prompt_version", "arena_group",
                   "status", "case_count", "scored_count", "passed_count", "error_count",
                   "aggregate_score", "avg_latency_seconds", "total_cost_usd", "peak_vram_mb",
                   "note", "created_at", "finished_at")


def _session_out(s: dict) -> dict:
    out = {k: s.get(k) for k in _SESSION_FIELDS}
    for k in ("context_settings", "case_filter"):
        try:
            out[k] = json.loads(s.get(k) or "{}")
        except ValueError:
            out[k] = {}
    out["note"] = redact(out["note"])
    return out


def close_stale_runs():
    """A run left queued/running with no live job (the app was closed
    mid-run) is marked interrupted, so it doesn't read "running" forever."""
    if _job_active():
        return
    for s in db.list_benchmark_sessions(200):
        if s.get("status") in ("queued", "running"):
            db.update_benchmark_session(s["id"], status="failed", finished_at=_now(),
                                        note="Interrupted (the app stopped during the run).")


def list_runs(stage: str = None, limit: int = 50) -> dict:
    if stage is not None and stage not in STAGES:
        raise InvalidInputError("Unknown stage.")
    close_stale_runs()
    limit = max(1, min(int(limit or 50), 200))
    return {"runs": [_session_out(s) for s in db.list_benchmark_sessions(limit, stage)]}


def _result_out(r: dict) -> dict:
    return {"case_id": r.get("case_id"), "case_label": r.get("case_label") or "",
            "output_text": r.get("output_text") or "", "score": r.get("score"),
            "metric": r.get("metric"), "scorer": r.get("scorer"), "passed": None if r.get("passed") is None else bool(r["passed"]),
            "duration_seconds": r.get("duration_seconds"), "cost_usd": r.get("cost_usd") or 0.0,
            "error": redact(r.get("error"))}


def get_run(run_id: int) -> dict:
    s = db.get_benchmark_session(run_id)
    if not s:
        raise NotFoundError("Benchmark run not found.")
    return {"run": _session_out(s),
            "results": [_result_out(r) for r in db.list_benchmark_results(run_id)]}


def arena(run_ids: list) -> dict:
    """Model Arena: two or more runs side by side, one row per case with
    each run's output and score, plus each run's aggregate and the score
    delta of every run against the first."""
    if not isinstance(run_ids, list) or not 2 <= len(run_ids) <= MAX_CONFIGS:
        raise InvalidInputError(f"Pick 2 to {MAX_CONFIGS} runs to compare.")
    if len(set(run_ids)) != len(run_ids):
        raise InvalidInputError("The same run is picked twice.")
    runs = []
    for rid in run_ids:
        s = db.get_benchmark_session(rid)
        if not s:
            raise NotFoundError("Benchmark run not found.")
        # A result whose case was deleted keeps its own row (keyed by result
        # id), never merged with another orphan under one None key.
        runs.append((s, {(r["case_id"] if r["case_id"] is not None else f"r{r['id']}"): r
                         for r in db.list_benchmark_results(rid)}))
    if len({s["stage"] for s, _ in runs}) > 1:
        raise InvalidInputError("Only runs of the same stage can be compared.")
    case_order = []
    for _s, results in runs:
        for cid in results:
            if cid not in case_order:
                case_order.append(cid)
    cases = {c["id"]: c for c in db.list_benchmark_cases(runs[0][0]["stage"])}
    rows = []
    for cid in case_order:
        c = cases.get(cid) or {}
        cells = [(_result_out(res[cid]) if cid in res else None) for _s, res in runs]
        label = c.get("label") or next((x["case_label"] for x in cells if x), "")
        rows.append({"case_id": cid if isinstance(cid, int) else None, "label": label, "source_text": c.get("source_text"),
                     "reference_text": c.get("reference_text"), "tier": c.get("tier"),
                     "results": cells})
    base = runs[0][0].get("aggregate_score")
    summary = []
    for s, _res in runs:
        out = _session_out(s)
        agg = s.get("aggregate_score")
        out["delta_vs_first"] = (agg - base) if agg is not None and base is not None else None
        summary.append(out)
    return {"runs": summary, "rows": rows}
