"""
services/translate_preset_service.py -- workflow tiers and translate presets for a title.

Applying a tier or a preset puts its engine on the drama row (the only field with a
per-drama DB home) and returns the rest for the client's form; saving writes the Translate
form as a preset. None of these starts a run or calls an engine.
"""
import sqlite3
from typing import Optional

import db
import translate_engines
import translation_guide
from services import library_service, settings_service, translate_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError
from services.translate_run_service import _is_safe_ollama_model, require_drama


def apply_workflow_tier(drama_id: int, tier: str) -> dict:
    """The tier's engine goes onto the drama row (the only field with a
    per-drama DB home); the model, Reflect and Auto QC are returned for the
    client to put into its form. Starts nothing."""
    drama = require_drama(drama_id)
    t = translate_engines.effective_tier(tier)
    if t is None:
        raise InvalidInputError("Unknown workflow tier.")
    if drama.get("translation_engine") != t["translation_engine"]:
        db.update_drama(drama_id, translation_engine=t["translation_engine"])
    return {"drama_id": drama_id, "tier": tier, "label": t["label"],
            "translation_engine": t["translation_engine"], "engine_model": t["engine_model"],
            "reflect": bool(t["reflect"]), "auto_qc": bool(t["auto_qc"])}


def apply_translate_preset(drama_id: int, preset_id: int) -> dict:
    """Applies a preset to an existing drama (parity X03): the preset's
    engine (if it saved one) goes onto the drama row, the one field with a
    per-drama DB home; style, locale, the two toggles and the model are
    returned for the client's form. Starts nothing.
    Raises NotFoundError (unknown drama or preset)."""
    require_drama(drama_id)
    preset = next((p for p in db.list_presets() if p["id"] == preset_id), None)
    if preset is None:
        raise NotFoundError(f"No preset with id {preset_id}.")
    engine = preset.get("translation_engine")
    if engine not in translate_engines.ENGINES:
        engine = None   # an engine this build no longer has: leave the drama's own
    if engine:
        db.update_drama(drama_id, translation_engine=engine)
    style = preset.get("style_preset")
    locale = preset.get("locale")
    pronouns = preset.get("default_female_pronouns")
    genre = preset.get("include_genre_notes")
    return {
        "drama_id": drama_id, "preset_id": preset["id"], "name": preset["name"],
        "translation_engine": engine,
        "engine_model": (preset.get("engine_model") or None) if engine else None,
        "style_preset": style if style in translation_guide.STYLE_PRESETS else None,
        "locale": locale if locale in settings_service.LOCALE_CHOICES else None,
        "default_female_pronouns": bool(pronouns),
        "include_genre_notes": True if genre is None else bool(genre),
    }


_PRESET_NAME_MAX = 100   # services/library_service._clean_name's limit


def save_translate_preset(name: str, translation_engine: str, engine_model: Optional[str] = None,
                          style_preset: Optional[str] = None, locale: Optional[str] = None,
                          default_female_pronouns: bool = False,
                          include_genre_notes: bool = True, overwrite: bool = False) -> dict:
    """Saves the Translate form as a preset: db.save_preset with the Translate form's engine,
    model, style, locale and the two toggles. Replacing a preset of the
    same name needs overwrite=True (otherwise ConflictError). Replacing is effectively a
    delete, so the route only allows overwrite from the PC. engine_model is None for engines without a
    model picker, and None (engine default) is allowed for the others."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > _PRESET_NAME_MAX:
        raise InvalidInputError(f"A preset name is 1-{_PRESET_NAME_MAX} characters.")
    if translation_engine not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(translation_engine))
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
    fields = dict(translation_engine=translation_engine, engine_model=engine_model,
                  style_preset=style_preset, locale=locale,
                  default_female_pronouns=bool(default_female_pronouns),
                  include_genre_notes=bool(include_genre_notes))
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
