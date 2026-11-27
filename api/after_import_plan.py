"""Owns the engine pick and permission gates for a novel's unattended text, lines, names and translate run."""
from typing import NamedTuple, Optional

from fastapi import Request

from api.auth import engine_for_caller, require_cloud_model_allowed, require_engines_allowed
from services import glossary_extract_service, page_run_service, translate_run_service


class NovelPlan(NamedTuple):
    engine: str
    model: Optional[str]
    lines: dict
    names: dict


def novel_plan(request: Request, drama_id: int) -> NovelPlan:
    """The engines are chosen here, never by the client: an engine the account may not use is swapped
    for the one every account may (`engine_for_caller`), and each step is held to its own gate."""
    default = translate_run_service.run_engine_name(drama_id)
    default_model = (translate_run_service.run_model_and_batch_size(drama_id, default)[0]
                     if default == "ollama" else None)
    engine, model = engine_for_caller(request, None, default, default_model)
    require_engines_allowed(request, engine, model=model)
    require_cloud_model_allowed(request, (engine, model))
    # Names and lines take the same server pick as the translation: no client choice, and an engine
    # the account may not use is swapped for the one every account may.
    names_engine, names_model = engine_for_caller(
        request, None, glossary_extract_service.novel_glossary_engine(drama_id),
        glossary_extract_service.glossary_model(drama_id))
    require_engines_allowed(request, names_engine, model=names_model)
    lines = page_run_service.lines_choice(drama_id, {})
    if not lines["one_narrator"]:
        lines["engine"], lines_model = engine_for_caller(request, None, lines["engine"], lines.get("model"))
        if lines_model:
            lines["model"] = lines_model
        require_engines_allowed(request, lines["engine"], model=lines.get("model"))
    page_run_service.require_cap_for_after_import(engine, model)
    return NovelPlan(engine, model, lines,
                     {"engine": names_engine, **({"model": names_model} if names_model else {})})
