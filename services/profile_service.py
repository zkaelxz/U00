"""
services/profile_service.py -- translation setting profiles: the saved presets
(db/presets.py), widened to carry the Translate run options and the transcribe
options as well as the engine, model, style and the two prompt toggles.

A profile is a one-time fill-in, never a live link to a title. Applying one
(at New title or later) writes:
  - the title's own columns, through the series-defaults rules: engine, the two
    prompt toggles, translate-by-sentence and the transcribe options. A value
    that does not suit the title's language (Whisper size, recognizer) is
    skipped and reported;
  - dramas.translate_start_json, the Translate form's starting values (model,
    style, style note, Reflect, bulk, thinking, batch size, context window,
    cost cap, fallbacks), because those have no column of their own.
Nothing starts a run or calls an engine.

A profile never carries the source language, the English variant, the glossary
or the language packs. Values are checked with the series-defaults validator
(same keys, ranges and transcribe rules), so there is no second one. Paid
engines are not special-cased here: choosing an engine spends nothing, and every
run checks the engine it uses (api.auth.require_engines_allowed).

No FastAPI import.
"""
import json
import math

import db
import translate_engines
import translation_guide
from services import series_defaults_service, settings_service
from services.service_errors import InvalidInputError

# Settings the presets table already stores as columns; the rest of the series
# defaults keys (transcribe options, translate-by-sentence) go in options_json.
_COLUMN_SETTINGS = ("translation_engine", "include_genre_notes", "default_female_pronouns")
TITLE_OPTIONS = frozenset(series_defaults_service.SETTINGS) - set(_COLUMN_SETTINGS)

# An older preset (no options_json) keeps its old reach: the engine goes on the title and
# the form starts from its style, model and these two toggles, which the title does not store.
_LEGACY_TOGGLES = ("default_female_pronouns", "include_genre_notes")

MAX_FALLBACKS = 2
MAX_OPTIONS_BYTES = 8192
_INT_RANGES = {"batch_size": (1, 200), "context_window": (0, 100), "context_window_ahead": (0, 100)}
_BOOLS = ("reflect", "bulk", "thinking")
_STYLE_NOTE_MAX = 4000
_COST_CAP_MAX = 100000
RUN_OPTIONS = frozenset(_INT_RANGES) | set(_BOOLS) | {"style_note", "cost_cap", "fallback_engines"}


def _check_run_option(key, value):
    if key in _INT_RANGES:
        lo, hi = _INT_RANGES[key]
        if not (isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi):
            raise InvalidInputError(f"{key} must be a whole number from {lo} to {hi}.")
    elif key in _BOOLS:
        if not isinstance(value, bool):
            raise InvalidInputError(f"{key} must be true or false.")
    elif key == "style_note":
        if not isinstance(value, str) or len(value) > _STYLE_NOTE_MAX:
            raise InvalidInputError(f"style_note is text of at most {_STYLE_NOTE_MAX} characters.")
    elif key == "cost_cap":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        try:
            ok = ok and math.isfinite(value) and 0 <= value <= _COST_CAP_MAX
        except OverflowError:   # an int too large for a float
            ok = False
        if not ok:
            raise InvalidInputError("cost_cap is a dollar amount of 0 or more.")
    elif key == "fallback_engines":
        ok = (isinstance(value, list) and len(value) <= MAX_FALLBACKS
              and all(isinstance(e, str) and e in translate_engines.ENGINES for e in value)
              and len(set(value)) == len(value))
        if not ok:
            raise InvalidInputError(f"fallback_engines is up to {MAX_FALLBACKS} different known engines.")


def validate_options(options) -> dict:
    """The options to store: known keys only, each value checked. Raises
    InvalidInputError for anything else, including a language, locale or
    glossary key."""
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise InvalidInputError("Profile options must be an object of setting: value.")
    unknown = set(options) - TITLE_OPTIONS - RUN_OPTIONS
    if unknown:
        raise InvalidInputError("Unknown profile option: " + ", ".join(sorted(map(str, unknown))[:5]))
    series_defaults_service.validate({k: v for k, v in options.items() if k in TITLE_OPTIONS})
    for key in RUN_OPTIONS & set(options):
        _check_run_option(key, options[key])
    if len(json.dumps(options)) > MAX_OPTIONS_BYTES:
        raise InvalidInputError("Too many profile options.")
    return dict(options)


def _lenient(raw) -> dict:
    """Stored options with any value that no longer passes (an engine this
    build dropped) left out, so one stale value never breaks the page."""
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(stored, dict):
        return {}
    out = {}
    for key, value in stored.items():
        try:
            validate_options({key: value})
        except InvalidInputError:
            continue
        out[key] = value
    return out


def stored_options(preset: dict) -> dict:
    return _lenient(preset.get("options_json"))


def dump_options(options: dict):
    return json.dumps(options, sort_keys=True)


def translate_start(drama: dict) -> dict:
    """The Translate form's starting values on this title: what a profile left, then
    what the last run was started with (remember_run)."""
    try:
        raw = json.loads(drama.get("translate_start_json") or "{}")
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if key in RUN_OPTIONS:
            try:
                _check_run_option(key, value)
            except InvalidInputError:
                continue
            out[key] = value
        elif key == "style_preset" and value in translation_guide.STYLE_PRESETS:
            out[key] = value
        elif key in _LEGACY_TOGGLES and isinstance(value, bool):
            out[key] = value
        elif key == "engine_model" and isinstance(value, str) and 0 < len(value) <= 200:
            out[key] = value
        elif key == "locale" and value in settings_service.LOCALE_CHOICES:
            # Only a run writes it (remember_run): a profile never carries the English variant.
            out[key] = value
    return out


def title_run_defaults(drama: dict, engine_name: str, title_engine: str) -> dict:
    """What a run of this title that was not handed a value starts with: the values
    its last run (or an applied profile) left, so a re-run, a queued run and a
    Library batch repeat that run instead of the Settings defaults. The model is
    the title's engine's, so it applies only to a run on that engine."""
    start = translate_start(drama)
    out = {k: start[k] for k in ("style_preset", "style_note", "locale", "batch_size",
                                 "context_window", "context_window_ahead") if k in start}
    model = start.get("engine_model")
    # A cloud tag is paid work the request's own gate only sees when the client sends it,
    # so a run that was handed no model never picks one up here.
    if (engine_name == title_engine and model
            and not (engine_name == "ollama" and translate_engines.is_ollama_cloud_model(model))):
        out["model"] = model
    return out


def locale_and_note(drama: dict, default_locale: str, default_style_note: str):
    """(English variant, style note) for a run nobody is configuring: the title's last
    run's, else the Settings defaults (the Queue, the Library batch, the stronger-engine redo)."""
    start = translate_start(drama)
    return (start.get("locale") or default_locale,
            start["style_note"] if "style_note" in start else default_style_note)


def remember_run(drama_id: int, drama: dict, *, on_title_engine: bool, model, style_preset,
                 style_note, locale, batch_size, context_window, context_window_ahead) -> None:
    """Keeps the values a run was started with as the title's Translate starting
    values (translate_start_json), next to what a profile left there. A batch size
    the run did not pick (None) clears the old one, so the engine's default
    applies again. The spend modes (Reflect, bulk, cost cap, fallbacks) are not kept:
    a later run must not repeat a paid mode nobody chose this time."""
    # Read again: a profile applied while the run started would be lost otherwise.
    start = translate_start(db.get_drama(drama_id) or drama)
    start.update(style_preset=style_preset, style_note=style_note, locale=locale,
                 context_window=context_window, context_window_ahead=context_window_ahead)
    if batch_size is None:
        start.pop("batch_size", None)
    else:
        start["batch_size"] = batch_size
    if on_title_engine:
        if model is None:
            start.pop("engine_model", None)
        else:
            start["engine_model"] = model
    db.update_drama(drama_id, translate_start_json=dump_options(start))


def plan(preset: dict, drama: dict) -> dict:
    """What applying this profile to this title writes, without writing it:
    {"columns": {column: value}, "start": the Translate form's start values,
    "skipped": [keys that do not suit the title's language]}. `drama` needs
    only what the checks read (source_language) plus the columns' current
    values; the language itself is never written."""
    options = stored_options(preset)
    engine = preset.get("translation_engine")
    if engine not in translate_engines.ENGINES:
        engine = None   # an engine this build no longer has: leave the title's own
    genre = preset.get("include_genre_notes")
    genre = True if genre is None else bool(genre)
    pronouns = bool(preset.get("default_female_pronouns"))
    wanted = {}
    if engine:
        wanted["translation_engine"] = engine
    start = {}
    if preset.get("options_json") is None:
        # The toggle columns of an older preset are just their column defaults, so they must not
        # override a series default or what the title holds.
        start.update(default_female_pronouns=pronouns, include_genre_notes=genre)
    else:
        wanted.update({k: v for k, v in options.items() if k in TITLE_OPTIONS},
                      include_genre_notes=genre, default_female_pronouns=pronouns)
        start.update({k: v for k, v in options.items() if k in RUN_OPTIONS})
    columns, skipped = series_defaults_service.plan_for(drama, wanted)
    style = preset.get("style_preset")
    if style in translation_guide.STYLE_PRESETS:
        start["style_preset"] = style
    # The model belongs to the profile's own engine only.
    if engine and preset.get("engine_model"):
        start["engine_model"] = preset["engine_model"]
    columns["translate_start_json"] = dump_options(start)
    return {"columns": columns, "start": start, "skipped": skipped}


def apply_to_title(drama_id: int, preset: dict) -> dict:
    """Writes the plan onto an existing title (db.update_drama with column
    names from the series-defaults whitelist and one fixed literal)."""
    drama = db.get_drama(drama_id)
    result = plan(preset, drama)
    db.update_drama(drama_id, **result["columns"])
    return result


def editor_fields() -> list:
    """The field specs for the profile editor: the title settings a profile
    may hold (the same specs the series Defaults tab shows)."""
    return [f for f in series_defaults_service._fields() if f["key"] in TITLE_OPTIONS]


def view(preset: dict) -> dict:
    """A preset row as the API shows it: the stored options as an object and
    the default flag as a boolean."""
    return {**{k: preset.get(k) for k in (
        "id", "name", "translation_engine", "engine_model", "style_preset", "locale",
        "default_female_pronouns", "include_genre_notes")},
        "options": stored_options(preset), "is_default": bool(preset.get("is_default"))}
