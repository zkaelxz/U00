"""
services/translate_preset_service.py -- workflow tiers and translate presets for a title.

Applying a tier or a preset puts its engine on the drama row (the only field with a
per-drama DB home) and returns the rest for the client's form; saving writes the Translate
form as a preset. None of these starts a run or calls an engine.
"""
import sqlite3
from typing import Optional

import db
from engine_backends import engine_registry
import translation_guide
from services import library_service, profile_service, settings_service, translate_service
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services.ownership_service import require_drama
from services.translate_run_service import _is_safe_ollama_model


def apply_workflow_tier(drama_id: int, tier: str) -> dict:
    """The tier's engine goes onto the drama row (the only field with a
    per-drama DB home); the model, Reflect and Auto QC are returned for the
    client to put into its form. Starts nothing."""
    drama = require_drama(drama_id)
    t = engine_registry.effective_tier(tier)
    if t is None:
        raise InvalidInputError("Unknown workflow tier.")
    if drama.get("translation_engine") != t["translation_engine"]:
        db.update_drama(drama_id, translation_engine=t["translation_engine"])
    return {"drama_id": drama_id, "tier": tier, "label": t["label"],
            "translation_engine": t["translation_engine"], "engine_model": t["engine_model"],
            "reflect": bool(t["reflect"]), "auto_qc": bool(t["auto_qc"])}


def apply_translate_preset(drama_id: int, preset_id: int) -> dict:
    """Applies a profile to an existing drama: its engine, prompt toggles and
    transcribe options go onto the drama's columns and the Translate form's
    starting values onto the drama (services/profile_service.py), skipping
    anything that doesn't suit the title's language. Style, model and the run
    options are also returned for the client's form. The language is never
    touched. Starts nothing.
    Raises NotFoundError (unknown drama or preset)."""
    require_drama(drama_id)
    preset = next((p for p in db.list_presets() if p["id"] == preset_id), None)
    if preset is None:
        raise NotFoundError(f"No preset with id {preset_id}.")
    applied = profile_service.apply_to_title(drama_id, preset)
    start = applied["start"]
    engine = preset.get("translation_engine")
    if engine not in engine_registry.ENGINES:
        engine = None   # an engine this build no longer has: leave the drama's own
    locale = preset.get("locale")
    return {
        "drama_id": drama_id, "preset_id": preset["id"], "name": preset["name"],
        "translation_engine": engine,
        "engine_model": start.get("engine_model"),
        "style_preset": start.get("style_preset"),
        "locale": locale if locale in settings_service.LOCALE_CHOICES else None,
        "default_female_pronouns": bool(preset.get("default_female_pronouns")),
        "include_genre_notes": True if preset.get("include_genre_notes") is None
        else bool(preset.get("include_genre_notes")),
        "options": {k: v for k, v in start.items() if k in profile_service.RUN_OPTIONS},
        "skipped": applied["skipped"],
    }


_PRESET_NAME_MAX = 100   # services/library_service._clean_name's limit


def _clean_profile(name, translation_engine, engine_model, style_preset, locale,
                   default_female_pronouns, include_genre_notes, options) -> dict:
    """The validated fields db.insert_preset / save_preset / update_preset take."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > _PRESET_NAME_MAX:
        raise InvalidInputError(f"A preset name is 1-{_PRESET_NAME_MAX} characters.")
    if translation_engine not in engine_registry.ENGINES:
        raise InvalidInputError(engine_registry.unknown_engine_message(translation_engine))
    models = next((e["models"] for e in translate_service.list_engines()
                   if e["name"] == translation_engine), None)
    if engine_model is not None and not (
            _is_safe_ollama_model(engine_model) if translation_engine == "ollama"
            else models is not None and engine_model in models):
        raise InvalidInputError("That model isn't offered for this engine.")
    if style_preset is not None and style_preset not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if locale is not None and locale not in settings_service.LOCALE_CHOICES:
        raise InvalidInputError("Unknown English variant.")
    return dict(name=name, translation_engine=translation_engine, engine_model=engine_model,
                style_preset=style_preset, locale=locale,
                default_female_pronouns=bool(default_female_pronouns),
                include_genre_notes=bool(include_genre_notes),
                options_json=profile_service.dump_options(profile_service.validate_options(options)))


def save_translate_preset(name: str, translation_engine: str, engine_model: Optional[str] = None,
                          style_preset: Optional[str] = None, locale: Optional[str] = None,
                          default_female_pronouns: bool = False,
                          include_genre_notes: bool = True, overwrite: bool = False,
                          options: Optional[dict] = None) -> dict:
    """Saves the Translate form as a profile: db.save_preset with the Translate form's engine,
    model, style, locale, the two toggles and the other options (profile_service). Replacing a preset of the
    same name needs overwrite=True (otherwise ConflictError). Replacing is effectively a
    delete, so the route only allows overwrite from the PC. engine_model is None for engines without a
    model picker, and None (engine default) is allowed for the others."""
    fields = _clean_profile(name, translation_engine, engine_model, style_preset, locale,
                            default_female_pronouns, include_genre_notes, options)
    name = fields.pop("name")
    # Insert-only first: the UNIQUE(name) constraint decides, not a prior
    # lookup, so two concurrent saves can't both think the name is free.
    try:
        preset_id, replaced = db.insert_preset(name, **fields), False
    except sqlite3.IntegrityError:
        if not overwrite:
            raise ConflictError("A preset with that name already exists; save again with "
                                "overwrite to replace it.")
        preset_id, replaced = db.save_preset(name, **fields), True
    preset = next(p for p in library_service.list_presets() if p["id"] == preset_id)
    return {"preset": preset, "replaced": replaced}


def _listed(preset_id: int) -> dict:
    """The profile as listed; NotFoundError if it was deleted since the write."""
    found = next((p for p in library_service.list_presets() if p["id"] == preset_id), None)
    if found is None:
        raise NotFoundError(f"No preset with id {preset_id}.")
    return found


def update_translate_preset(preset_id: int, name: str, translation_engine: str,
                            engine_model: Optional[str] = None, style_preset: Optional[str] = None,
                            locale: Optional[str] = None, default_female_pronouns: bool = False,
                            include_genre_notes: bool = True,
                            options: Optional[dict] = None) -> dict:
    """Edit: replaces every field and the name of one profile (its id and
    default flag stay). The route only allows this from the PC, like overwrite.
    Raises NotFoundError (unknown id), ConflictError (name taken)."""
    fields = _clean_profile(name, translation_engine, engine_model, style_preset, locale,
                            default_female_pronouns, include_genre_notes, options)
    try:
        found = db.update_preset(preset_id, **fields)
    except sqlite3.IntegrityError:
        raise ConflictError(f"A preset named {fields['name']!r} already exists.") from None
    if not found:
        raise NotFoundError(f"No preset with id {preset_id}.")
    return {"preset": _listed(preset_id)}


def profile_editor_fields() -> dict:
    """What the profile editor offers; nothing here is a secret."""
    return {"engines": translate_service.list_engines(),
            "style_presets": [{"key": k, "label": v["label"], "guidance": v["guidance"]}
                              for k, v in translation_guide.STYLE_PRESETS.items()],
            "fields": profile_service.editor_fields()}


def set_default_preset(preset_id: int, is_default: bool) -> dict:
    """Marks this profile as the one New title preselects (or clears it)."""
    if not db.set_default_preset(preset_id, is_default):
        raise NotFoundError(f"No preset with id {preset_id}.")
    return _listed(preset_id)
