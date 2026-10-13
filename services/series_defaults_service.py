"""
services/series_defaults_service.py -- a series' default title settings: the
per-title translate and transcribe options a title already stores on its own
row (SETTINGS), kept as JSON on the series (series.default_settings).

  - A title created in the series starts with them (inherit_new); a title
    moved into it gets them only for settings it has no value for
    (inherit_moved). Both are best-effort: the caller's create or move
    stands even if this fails.
  - "Apply to all titles in this series": a preview, then one transaction
    that writes only those settings columns, refuses while a job runs for
    any of the series' titles, and refuses if a title changed since the
    plan was made (db.apply_series_settings). "Fill" counts a value still
    equal to the app's default as not set; titles the caller may not edit
    are left out.

Only the keys in SETTINGS are ever stored or written: they become SQL
column names in db.update_drama / db.apply_series_settings. No secret is a
setting: engine keys stay in Settings. A title setting whose own write needs
engines.paid (use_groq, translate_thinking) is never a default: defaults are
set with lines.edit and copied to every title. translation_engine is a
default even so: choosing an engine spends nothing, and every run checks the
engine it actually uses (api.auth.require_engines_allowed; a missing engine
counts as paid), so a paid default can't be run without engines.paid. Values
are checked with the same rules as a title's own
(transcribe_service.check_transcribe_fields, the engine registry); the checks that depend on a title's language run per
title, and a default that doesn't suit a title's language is skipped there.

Ownership: reading needs the series to be visible, writing and applying
need it editable (ownership_service), as for the rest of the Series page.

No FastAPI import.
"""
import json
import math

import core as core_module
import db
import segment_splitting
import sensitivity_preset as presets
import translate_engines
from services import (auth_service, ownership_service, series_service, settings_service,
                      transcribe_service)
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_JSON_BYTES = 4096
MODES = ("fill", "overwrite")
_CHANGED = ("A title in this series changed while the defaults were being applied. "
            "Preview again to see what would change now.")
_JOB_RUNNING = ("A job is running for a title in this series. Wait for it to finish or "
                "cancel it before applying the defaults.")


def _choice(label, group, choices):
    return {"label": label, "group": group, "type": "choice", "choices": tuple(choices)}


def _bool(label, group):
    return {"label": label, "group": group, "type": "bool"}


def _num(label, group, kind, lo, hi):
    return {"label": label, "group": group, "type": kind, "min": lo, "max": hi}


# key -> what the Defaults tab shows and the type a value must have. Ranges
# and choices mirror transcribe_service.check_transcribe_fields, which still
# has the last word on every transcribe value.
SETTINGS = {
    "translation_engine": _choice("Translation engine", "translate", translate_engines.ENGINES),
    "include_genre_notes": _bool("Genre notes in the prompt", "translate"),
    "default_female_pronouns": _bool("Default to she/her", "translate"),
    "translate_by_sentence": _bool("Translate by sentence", "translate"),
    "whisper_size": _choice("Whisper model", "transcribe",
                            sorted(transcribe_service._allowed_whisper_sizes())),
    "alignment_method": _choice("Alignment", "transcribe", ("whisper_diff", "qwen3_forced_align")),
    "asr_backend_choice": _choice("Speech recognizer", "transcribe",
                                  transcribe_service.ASR_BACKEND_CHOICES),
    "sensitivity_preset": _choice("Sensitivity", "transcribe", presets.PRESETS),
    "separation_backend": _choice("Vocal separation", "transcribe",
                                  transcribe_service._SEPARATION_BACKENDS),
    "beam_size": _num("Beam size", "transcribe", "int", 1, 10),
    "min_silence_ms": _num("Minimum silence (ms)", "transcribe", "int",
                           core_module.MIN_SILENCE_MS_MIN, core_module.MIN_SILENCE_MS_MAX),
    "vad_threshold": _num("Voice detection threshold", "transcribe", "number", 0.1, 0.9),
    "hallucination_silence_sec": _num("Skip silences longer than (s, 0 = off)", "transcribe",
                                      "number", 0, 10),
    "min_pause_sec": _num("Split long lines at pauses of (s)", "transcribe", "number",
                          segment_splitting.MIN_WORD_GAP_SECONDS_MIN,
                          segment_splitting.MIN_WORD_GAP_SECONDS_MAX),
    "separate_vocals_first": _bool("Separate vocals first", "transcribe"),
    "realign_long_segments": _bool("Re-align long lines", "transcribe"),
    "whisper_fast_mode": _bool("Fast mode", "transcribe"),
    "whisper_repeat_guard": _bool("Repeat guard", "transcribe"),
    "split_by_sentences": _bool("Split by sentences", "transcribe"),
    "difficult_audio": _bool("Difficult audio", "transcribe"),
    "vocabulary_hint": _bool("Vocabulary hint", "transcribe"),
}
_ALLOWED = frozenset(SETTINGS)
_TRANSCRIBE = frozenset(k for k, f in SETTINGS.items() if f["group"] == "transcribe")
# A title's value that equals the app's own default counts as "not set by the
# title" (most columns have a database default, so NULL alone can't tell).
_BUILTIN = {k: v for k, v in transcribe_service._DEFAULT_TUNING.items() if k in _ALLOWED}
_BUILTIN.update(alignment_method="whisper_diff", whisper_repeat_guard=False,
                split_by_sentences=False, difficult_audio=False)


def _check_type(key, value):
    kind = SETTINGS[key]["type"]
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        try:
            ok = ok and math.isfinite(value)
        except OverflowError:   # an int too large for a float
            ok = False
    else:
        ok = isinstance(value, str)
    if not ok:
        raise InvalidInputError(f"{key} has the wrong type.")


def _check_value(key, value):
    """Type, then the same rule a title's own value obeys (choices, range)."""
    _check_type(key, value)
    spec = SETTINGS[key]
    if spec["type"] == "choice" and value not in spec["choices"]:
        raise InvalidInputError(f"Unknown {key}.")
    if spec["type"] in ("int", "number") and not spec["min"] <= value <= spec["max"]:
        raise InvalidInputError(f"{key} must be between {spec['min']} and {spec['max']}.")
    if key in _TRANSCRIBE:
        transcribe_service.check_transcribe_fields({key: value}, None)


def validate(settings) -> dict:
    """The settings to store: known keys only, each value checked."""
    if not isinstance(settings, dict):
        raise InvalidInputError("Default settings must be an object of setting: value.")
    unknown = set(settings) - _ALLOWED
    if unknown:
        raise InvalidInputError("Unknown setting: " + ", ".join(sorted(map(str, unknown))[:5]))
    for key, value in settings.items():
        _check_value(key, value)
    if len(json.dumps(settings)) > MAX_JSON_BYTES:
        raise InvalidInputError("Too many default settings.")
    return dict(settings)


def _stored(row) -> dict:
    """The series' stored defaults. A value that no longer passes (an engine
    or model this build dropped) is left out rather than failing the page."""
    try:
        raw = json.loads(row.get("default_settings") or "{}")
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if key in _ALLOWED:
            try:
                _check_value(key, value)
            except InvalidInputError:
                continue
            out[key] = value
    return out


def _series_row(series_id) -> dict:
    row = next((s for s in db.list_series() if s["id"] == series_id), None)
    if row is None:
        raise NotFoundError("Series not found.")
    return row


def _fields() -> list:
    return [{"key": k, **{n: list(v) if n == "choices" else v for n, v in spec.items()}}
            for k, spec in SETTINGS.items()]


def get_defaults(principal, series_id) -> dict:
    ownership_service.require_visible(principal, "series", series_id)
    return {"series_id": series_id, "settings": _stored(_series_row(series_id)),
            "fields": _fields()}


def set_defaults(principal, series_id, settings) -> dict:
    """Replaces the series' defaults ({} clears them)."""
    ownership_service.require_editable(principal, "series", series_id)
    clean = validate(settings)
    if not db.set_series_defaults(series_id, json.dumps(clean) if clean else None):
        raise NotFoundError("Series not found.")
    return {"series_id": series_id, "settings": clean, "fields": _fields()}


# --- planning ----------------------------------------------------------------

def _as_column(key, value):
    return int(value) if SETTINGS[key]["type"] == "bool" else value


def _as_value(key, column):
    """A title's column as a settings value (0/1 -> bool)."""
    if column is None:
        return None
    return bool(column) if SETTINGS[key]["type"] == "bool" else column


def _builtin(key):
    if key == "translation_engine":
        return settings_service.get("default_engine")
    return _BUILTIN.get(key)


def _unset(key, column) -> bool:
    value = _as_value(key, column)
    return value is None or value == _builtin(key)


def _suits_title(key, value, drama) -> bool:
    if key not in ("whisper_size", "asr_backend_choice"):
        return True
    try:
        transcribe_service.check_transcribe_fields({key: value},
                                                   drama.get("source_language") or "zh")
    except InvalidInputError:
        return False
    return True


def _plan_title(drama, defaults, mode, skip=()) -> tuple:
    """({column: new}, {column: old}, [keys skipped for this title's language]).
    mode: "overwrite"; "fill" (NULL or still at the app default); "null"
    (NULL only: a move never replaces a value the title holds, even one equal
    to the app default it may have chosen on purpose)."""
    new, old, unsuited = {}, {}, []
    for key, value in defaults.items():
        if key in skip:
            continue
        column = drama.get(key)
        if mode == "fill" and not _unset(key, column):
            continue
        if mode == "null" and column is not None:
            continue
        if _as_value(key, column) == value:
            continue
        if not _suits_title(key, value, drama):
            unsuited.append(key)
            continue
        new[key], old[key] = _as_column(key, value), column
    return new, old, unsuited


def _inherit(drama_id, series_id, mode, skip=()) -> None:
    defaults = _stored(_series_row(series_id))
    drama = db.get_drama(drama_id)
    if not defaults or drama is None:
        return
    new, _old, _unsuited = _plan_title(drama, defaults, mode, skip)
    if new:
        db.update_drama(drama_id, **new)


def inherit_new(drama_id, series_id, skip=()) -> None:
    """A title just created in the series: every default applies (it has set
    nothing itself), except `skip` (what the create call chose, e.g. a
    preset's engine). The caller has already checked the series."""
    _inherit(drama_id, series_id, "overwrite", skip)


def inherit_moved(drama_id, series_id) -> None:
    """A title just moved into the series: defaults fill only the settings it
    has no value for (NULL); every value it holds stays."""
    _inherit(drama_id, series_id, "null")


def _check_mode(mode):
    if mode not in MODES:
        raise InvalidInputError("mode must be fill or overwrite.")


def _job_running(series_id) -> bool:
    return series_service.job_running_for_series(series_id)


def _plan(principal, series_id, mode) -> tuple:
    """(plan for db.apply_series_settings, the titles it changes, listed).
    A title the caller may not edit (a legacy private title of someone else
    in a shared series) stays in the plan with nothing to write, so the
    transaction still checks the series' titles, and is neither counted nor
    shown."""
    defaults = _stored(_series_row(series_id))
    plan, titles = {}, []
    for drama in db.list_dramas_by_series(series_id):
        if not ownership_service.can_edit_drama(principal, drama["id"]):
            plan[drama["id"]] = {"old": {}, "new": {}}
            continue
        new, old, unsuited = _plan_title(drama, defaults, mode)
        plan[drama["id"]] = {"old": old, "new": new}
        titles.append({"id": drama["id"], "title_en": drama.get("title_en"),
                       "title_zh": drama.get("title_zh"),
                       "changes": {k: {"from": _as_value(k, old[k]), "to": defaults[k]}
                                   for k in new},
                       "unsuited": unsuited})
    return plan, titles


def apply_preview(principal, series_id, mode) -> dict:
    """What "Apply to all titles" would change, per title the caller may
    edit. Writes nothing."""
    ownership_service.require_editable(principal, "series", series_id)
    _check_mode(mode)
    _, titles = _plan(principal, series_id, mode)
    return {"series_id": series_id, "mode": mode, "title_count": len(titles),
            "changed_titles": sum(1 for t in titles if t["changes"]),
            "fields_changed": sum(len(t["changes"]) for t in titles),
            "job_running": _job_running(series_id), "titles": titles}


def apply_defaults(principal, series_id, mode) -> dict:
    """Recomputes the plan (never trusts a preview), refuses while a job runs
    for any title in the series, then writes it in one transaction that
    refuses if a title changed since the plan. Audit: counts only."""
    ownership_service.require_editable(principal, "series", series_id)
    _check_mode(mode)
    plan, titles = _plan(principal, series_id, mode)
    if _job_running(series_id):
        raise ConflictError(_JOB_RUNNING)
    changed = {d: c for d, c in plan.items() if c["new"]}
    titles_changed = len(changed)
    fields_changed = sum(len(c["new"]) for c in changed.values())
    if not db.apply_series_settings(series_id, plan, _ALLOWED):
        raise ConflictError(_CHANGED)
    auth_service.write_audit(
        ownership_service.user_id(principal), "series.apply_defaults",
        f"series {series_id}: mode={mode} titles={len(titles)} changed_titles={titles_changed} "
        f"fields={fields_changed}")
    return {"series_id": series_id, "mode": mode, "title_count": len(titles),
            "titles_changed": titles_changed, "fields_changed": fields_changed}
