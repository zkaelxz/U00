"""
services/bulk_submit_service.py -- builds the zero-argument callable that submits a
bulk translation (provider batch, bulk Reflect, or the DeepSeek off-peak schedule)
from inside the background job, so the provider call never blocks the request.
"""
import bulk_reflect
import bulk_translate
import db
import translate_engines
import translation_guide
from services import settings_service


def bulk_submitter(drama_id, drama, engine, engine_name, reflect, novel_reference,
                    glossary_terms, style_guidelines, style_note, locale, style_preset,
                    context_window, context_window_ahead, batch_size, force_retranslate,
                    job_cost_cap_usd, series_id, thinking):
    """A zero-arg callable that submits the bulk translation (or bulk
    Reflect) batch (same translate_args, context and character names as a
    normal run) and returns the bulk job id. Called inside the job so the provider
    call never blocks the request. Lines are re-read from the DB at submit
    time so each carries its permanent id and current English."""
    def submit() -> int:
        lines = db.load_line_objects(drama_id)
        if force_retranslate and any(ln.en for ln in lines):
            db.save_line_history_snapshot(drama_id, lines, "before force re-translate")
        if reflect:
            return bulk_reflect.submit_reflect_pipeline(
                drama_id, lines, engine, engine_name,
                {"style_note": style_note, "drama_meta": drama, "novel_reference": novel_reference,
                 "locale": locale, "glossary_terms": glossary_terms,
                 "style_guidelines": style_guidelines, "style_preset": style_preset},
                batch_size=batch_size, force_retranslate=force_retranslate)
        if engine_name == "deepseek":
            # the run's own thinking: the job starts later
            return bulk_translate.schedule_offpeak_translation(
                drama_id, lines, engine_name, getattr(engine, "model", ""),
                {"style_note": style_note, "locale": locale, "glossary_terms": glossary_terms,
                 "style_guidelines": style_guidelines, "style_preset": style_preset,
                 "context_window": context_window, "cost_cap_usd": job_cost_cap_usd or None,
                 "novel_reference": novel_reference, "thinking": thinking, "batch_size": batch_size,
                 "context_window_ahead": context_window_ahead,
                 **settings_service.run_options()},
                force_retranslate=force_retranslate)
        character_names = translation_guide.build_speaker_labels(
            db.list_characters_with_series_names(drama_id),
            db.list_series_characters(series_id) if series_id else [])
        context = translate_engines.build_translation_context(
            engine, drama, style_note=style_note, novel_reference=novel_reference, locale=locale,
            glossary_terms=glossary_terms, style_guidelines=style_guidelines)
        return bulk_translate.submit_bulk_translation(
            drama_id, lines, engine, engine_name, context,
            translate_args={"glossary_terms": glossary_terms, "style_preset": style_preset},
            force_retranslate=force_retranslate, context_window=context_window,
            context_window_ahead=context_window_ahead, batch_size=batch_size,
            character_names=character_names)
    return submit
