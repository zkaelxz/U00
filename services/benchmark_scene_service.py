"""
services/benchmark_scene_service.py -- Benchmark Lab translation arms that translate the way a
title is translated: the cases of one title run as batches in order through the same pipeline a
title's Translate uses (batch size, context window, Reflect, style preset), optionally with a
series' glossary, learned style and character hints. An arm without `options` keeps the older
one-case-at-a-time run, so earlier results stay comparable. The run loop, scoring and
persistence stay in benchmark_lab_service.
"""

import math
import time

import adaptive_style
import db
import translate_engines
import translation_guide as tguide
from core import SOURCE_LANGUAGES, Line
from engine_backends import paid_calls, pricing
from services import transcribe_options_service
from services.benchmark_case_service import redact
from services.service_errors import InvalidInputError

DEFAULT_STYLE_PRESET = "audio_drama"
# The ranges a saved profile accepts (profile_service), so a swept winner can be saved as one.
_INT_RANGES = {"batch_size": (1, 200), "context_window": (0, 100), "context_window_ahead": (0, 100)}
OPTION_KEYS = (*_INT_RANGES, "reflect", "style_preset", "series_id")
OPTION_LABELS = {"batch_size": "Batch size", "context_window": "Context lines before",
                 "context_window_ahead": "Context lines after", "reflect": "Reflect",
                 "style_preset": "Style preset", "series_id": "Series"}


def defaults(engine: str, model) -> dict:
    """What a title's Translate starts with for this engine and model."""
    base = translate_engines.default_translate_options(engine, model or translate_engines.effective_default_model(engine))
    return {**base, "reflect": False, "style_preset": DEFAULT_STYLE_PRESET, "series_id": None}


def resolve_options(engine: str, model, raw) -> dict:
    """The complete option set of an arm: a value left out takes the app's default, anything
    unknown or out of range is refused."""
    if not isinstance(raw, dict):
        raise InvalidInputError("Translation options must be an object of setting: value.")
    if set(raw) & set(transcribe_options_service.lab_option_defaults()):
        raise InvalidInputError("Transcription options apply to transcription runs only.")
    unknown = set(raw) - set(OPTION_KEYS)
    if unknown:
        raise InvalidInputError("Unknown translation option: " + ", ".join(sorted(map(str, unknown))[:5]))
    options = defaults(engine, model)
    for key, value in raw.items():
        if value is None:
            continue
        if key in _INT_RANGES:
            low, high = _INT_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise InvalidInputError(f"{OPTION_LABELS[key]} must be a whole number from {low} to {high}.")
        elif key == "reflect":
            if not isinstance(value, bool):
                raise InvalidInputError("Reflect must be on or off.")
        elif key == "style_preset":
            if value not in tguide.STYLE_PRESETS:
                raise InvalidInputError("Unknown style preset.")
        elif isinstance(value, bool) or not isinstance(value, int) or not any(
                s["id"] == value for s in db.list_series()):
            raise InvalidInputError("That series doesn't exist.")
        options[key] = value
    return options


def changes(engine: str, model, options: dict) -> dict:
    """What the arm changed from the defaults: what tells two arms of one model apart in a list."""
    base = defaults(engine, model)
    return {k: v for k, v in options.items() if v != base[k]}


def run_context(cfg: dict) -> dict:
    return {"translate_options": cfg["options"], "translate_scenes": True,
            "translate_changes": changes(cfg["engine"], cfg["model"], cfg["options"])}


def scenes(cases: list) -> list:
    """The cases grouped into scenes, each in its place order. A title is one scene; a case without
    a title is a scene of its own, so a hand-typed case is translated alone as before."""
    groups = {}
    for case in cases:
        title = (case.get("title") or "").strip()
        groups.setdefault(title or ("", case["id"]), []).append(case)
    return [sorted(group, key=lambda c: (c.get("scene_order") is None, c.get("scene_order") or 0, c["id"]))
            for group in groups.values()]


def style_context(options: dict) -> tuple:
    """(glossary terms, style guidelines) for the arm. Only a chosen series adds its glossary,
    learned style and character hints: the Lab has no title to read them from. The run starts
    on the PC (a PC-only route), so the learned style is not held back as it is from members."""
    series_id = options["series_id"]
    glossary = db.list_glossary_terms(series_id) if series_id else None
    notes = []
    if series_id:
        profile = db.get_style_profile(f"series:{series_id}")
        if profile:
            notes.append(adaptive_style.profile_to_prompt_block(profile.get("profile", {})))
        notes.append(tguide.build_character_gender_hints(db.list_series_characters(series_id), None, False))
    guidelines = tguide.build_style_guidelines(
        options["style_preset"], glossary_terms=glossary, include_genre_notes=True,
        default_female_pronouns=False, custom_notes="\n\n".join(n for n in notes if n))
    return glossary, guidelines


def estimate_cost(probe, options: dict, cases: list) -> float:
    """Pre-run cost of one arm: the plain estimate per scene, plus the context lines and the style
    block every batch re-sends, three times over for Reflect."""
    guidelines = style_context(options)[1]
    total = 0.0
    for scene in scenes(cases):
        lines = [c.get("source_text") or "" for c in scene]
        batches = math.ceil(len(lines) / options["batch_size"])
        average = sum(map(len, lines)) / len(lines)
        tokens_in, tokens_out = pricing.estimate_translation_tokens(lines, options["batch_size"])
        context = batches * (options["context_window"] + options["context_window_ahead"]) * average
        tokens_in += int((context + batches * len(guidelines)) / 3.5)
        total += translate_engines.estimate_cost_for_engine(probe, tokens_in, tokens_out)
    return total * (3 if options["reflect"] else 1)


class SceneRunner:
    """Translates a run's cases scene by scene, the first time a case of the scene is asked for,
    and hands each case its own result in the shape the run loop expects."""

    def __init__(self, engine, cfg: dict, cases: list, is_cancelled, api_key=None):
        self.engine, self.cfg, self.is_cancelled, self.api_key = engine, cfg, is_cancelled, api_key
        self.scene_of = {c["id"]: scene for scene in scenes(cases) for c in scene}
        self.results = {}
        self.glossary, self.guidelines = style_context(cfg["options"])

    def result(self, case: dict, remaining_usd=None) -> dict:
        """remaining_usd: what the run may still spend, None for no cap; the scene stops after the
        batch that reaches it."""
        if case["id"] not in self.results:
            self.results.update(self._translate(self.scene_of[case["id"]], remaining_usd))
        return self.results.pop(case["id"])

    def _translate(self, scene: list, remaining_usd) -> dict:
        options, name = self.cfg["options"], self.cfg["engine"]
        lang = scene[0].get("source_language") if scene[0].get("source_language") in SOURCE_LANGUAGES else "zh"
        lines = [Line(idx=i, start=float(i), end=i + 0.5, zh=c.get("source_text") or "", id=c["id"])
                 for i, c in enumerate(scene)]
        spent = [0.0]

        def usage_cb(tokens_in, tokens_out, cache_read=0, cache_write=0):
            cost = translate_engines.estimate_cost_for_engine(self.engine, tokens_in, tokens_out, cache_read, cache_write)
            spent[0] += cost
            db.log_usage(None, name, getattr(self.engine, "model", self.cfg["model"]) or "", "benchmark",
                         tokens_in, tokens_out, cost, cache_read_tokens=cache_read)

        started = time.monotonic()
        errors, failure, stop_reason = [], None, ""
        try:
            _, errors = translate_engines.translate_lines_with_engine(
                lines, self.engine, drama_meta={"source_language": lang}, batch_size=options["batch_size"],
                locale="en-US", glossary_terms=self.glossary, style_guidelines=self.guidelines,
                context_window=options["context_window"], context_window_ahead=options["context_window_ahead"],
                reflect=options["reflect"], scene_aware_batches=False, translate_by_sentence=False,
                cost_cap_usd=remaining_usd, cancel_check_cb=self.is_cancelled, usage_cb=usage_cb)
        except Exception as exc:
            failure, stop_reason = redact(exc, self.api_key), paid_calls.stop_reason_for(exc)
        # Matched back to the case by its id, never by position.
        failed = {i: e.get("error") for e in errors or [] for i in e.get("line_ids") or []}
        capped = remaining_usd is not None and spent[0] >= remaining_usd
        weight = {ln.id: max(len(ln.zh), 1) for ln in lines}
        elapsed = time.monotonic() - started
        out = {}
        for ln in lines:
            share = weight[ln.id] / sum(weight.values())
            error = None
            if not ln.en:
                error = failure or redact(failed.get(ln.id), self.api_key) or (
                    "Stopped at the spending cap." if capped else "The engine returned no translation for this line.")
            out[ln.id] = {"output_text": ln.en or "", "error": error, "cost_usd": spent[0] * share, "usage": {},
                          "duration_seconds": elapsed * share, "stop_reason": stop_reason, "usage_logged": True}
        return out


def option_choices() -> dict:
    """What the Lab form offers for a translation arm: the style presets, the series whose
    glossary can be used, and the defaults a title starts with."""
    from services import translate_service
    engines = translate_service.list_engines()
    return {"style_presets": [{"key": k, "label": v["label"]} for k, v in tguide.STYLE_PRESETS.items()],
            "series": [{"id": s["id"], "name": s["name"]} for s in db.list_series()],
            "defaults": {**translate_engines.default_translate_options(), "reflect": False,
                         "style_preset": DEFAULT_STYLE_PRESET},
            "batch_size_defaults": translate_engines.batch_size_defaults(engines)}
