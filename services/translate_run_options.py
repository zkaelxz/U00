"""
services/translate_run_options.py -- the request checks of start_translate_run:
which options may combine, and which lines a run will translate.
"""

import hashlib
import json
import time

from bulk import bulk_translate
import db
from engine_backends import local, engine_registry
from engine_backends import fallback as engine_fallback
from services import ownership_service, paid_job_service, settings_service
from lib import http
from lib.errors import (
    ConflictError,
    InvalidInputError,
    UnsupportedOperationError,
)


def ollama_reachable() -> bool:
    """Parity X24: whether the configured Ollama server answers (the
    "Can't reach Ollama" warning). A boolean only; the URL never leaves
    the server. Cached briefly by engine_backends."""
    return bool(local.check_ollama_reachable(
        settings_service.ollama_url()))


def chapter_line_ids(drama_id, chapter_from, chapter_to, line_ids=None):
    """The ids of the lines of chapters chapter_from..chapter_to (1-based, inclusive; one alone
    leaves that side open), or `line_ids` unchanged when no chapter is chosen. Chapters are found
    the way chunk & tag cuts them, so "Chapter 3" is the same text in both steps."""
    if chapter_from is None and chapter_to is None:
        return line_ids
    if line_ids is not None:
        raise InvalidInputError("Choose chapters or lines, not both.")
    chapters = _chapter_line_id_lists(drama_id)
    first, last = chapter_from or 1, chapter_to or len(chapters)
    if not 1 <= first <= last <= len(chapters):
        raise InvalidInputError(f"Choose chapters from 1 to {len(chapters)} (this title has {len(chapters)}).")
    if (first, last) == (1, len(chapters)):
        return None
    picked = [i for ids in chapters[first - 1:last] for i in ids]
    if not picked:
        raise InvalidInputError("Those chapters have no lines yet. Make their lines with Split and name speakers first.")
    return picked


def _chapter_line_id_lists(drama_id) -> list:
    """Each chapter's line ids, chapters numbered from the title's saved novel text as
    Chunk & tag numbers them, so a title chunked from chapter 50 on has its lines in
    chapter 50, not chapter 1. Lines that no longer match that text (edited or merged in
    Review) are cut at their own headings, and each piece goes to the chapter of the text
    with the same heading. Without saved text, the lines' own headings number them."""
    import novel_text
    from services import narration_service
    lines = db.load_line_objects(drama_id)
    own = [lines[a:b + 1] for a, b in novel_text.chapter_line_ranges([ln.zh or "" for ln in lines])]
    source = narration_service._read_novel(drama_id)
    if not source.strip():
        return [[ln.id for ln in chapter] for chapter in own]
    try:
        by_chapter = narration_service._align_chapters(narration_service._chapter_chunks(source), lines)
    except ConflictError:
        by_chapter = _placed_by_heading(source, own)
    if by_chapter is None:
        # Numbering by the lines' own headings would pick other chapters than the ones asked for.
        raise InvalidInputError("A chapter heading was edited in Review, so the lines no longer match the "
                                "novel's chapters. Translate all chapters instead.")
    return [[ln.id for ln in chapter] for chapter in by_chapter]


def _squashed(text) -> str:
    return "".join((text or "").split())


def _placed_by_heading(source: str, pieces: list):
    """The pieces (lines cut at their own headings, in order) at the chapter of `source`
    whose opening heading each starts with; the lines before the first heading go to the
    text's untitled opening. None when a piece matches no chapter after the previous one."""
    import novel_text
    heads = []
    for a, b in novel_text.chapter_spans(source):
        opening = []
        for row in source[a:b].splitlines():
            if not row.strip():
                continue
            if not novel_text.is_heading_text(row):
                break
            opening.append(_squashed(row))
        heads.append(opening)
    out, pos = [[] for _ in heads], 0
    for n, piece in enumerate(pieces):
        if not piece:
            continue
        first = _squashed(piece[0].zh)
        found = next((k for k in range(pos, len(heads)) if first in heads[k]), None)
        if found is None and n == 0 and heads and not heads[0] and not novel_text.is_heading_text(piece[0].zh):
            found = 0
        if found is None:
            return None
        out[found], pos = piece, found + 1
    return out


def check_mode_options(engine_name, gemini_free_tier, reflect, bulk, fallback_chain,
                        own_lines_only, line_ids) -> None:
    if fallback_chain and (reflect or bulk):
        raise InvalidInputError("A fallback chain only applies to a normal translation run.")
    if own_lines_only and line_ids is None:
        raise InvalidInputError("own_lines_only needs line_ids.")
    if bulk and line_ids is not None:
        raise InvalidInputError("Bulk mode translates the whole title; line_ids isn't supported.")
    if reflect and engine_name in engine_registry.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(f"{engine_name} can't run Reflect mode.")
    if bulk and (engine_name not in bulk_translate.BULK_ENGINES
                 or (engine_name == "gemini" and gemini_free_tier)):
        raise UnsupportedOperationError("Bulk mode needs Claude, Gemini (paid) or DeepSeek.")
    if bulk and reflect and engine_name == "deepseek":
        raise UnsupportedOperationError("Bulk Reflect needs Claude or Gemini's batch API.")


BULK_IN_FLIGHT_STATUSES = db.BULK_PENDING_STATUSES + ("submitting", "running", "applying")


def select_lines(drama_id, line_ids, expected_en, force_retranslate):
    """(all lines, target ids or None, lines this run will translate)."""
    lines = db.load_line_objects(drama_id)
    if not lines:
        raise UnsupportedOperationError("This title has no lines to translate yet.")
    target_ids = None
    if line_ids is not None:
        target_ids = set(line_ids)
        if not target_ids or not target_ids <= {ln.id for ln in lines}:
            raise InvalidInputError("line_ids must be a non-empty list of this title's line ids.")
        if expected_en is not None:
            target_ids = {ln.id for ln in lines if ln.id in target_ids
                          and (ln.en or "") == expected_en.get(ln.id)}
            if not target_ids:
                raise ConflictError("The chosen lines were edited since they were picked. "
                                    "Pick them again.", details={"reason": "stale_preview"})
    eligible = [ln for ln in lines if (ln.zh or "").strip()
                and (force_retranslate or not (ln.en or "").strip())
                and (target_ids is None or ln.id in target_ids)]
    if not eligible:
        raise UnsupportedOperationError("There are no lines to translate.")
    if force_retranslate and any((ln.en or "").strip() for ln in eligible):
        ownership_service.require_acting_lines_edit()
    return lines, target_ids, eligible


def instantiate_engines(built, chain, caps, slow_switch=False):
    """(engines, the engine the run uses, its cost cap). With a fallback chain the
    run uses a FallbackEngine over all of them; else, with slow_switch, a DeepSeek
    run gets with_slow_switch."""
    engines = [engine_registry.get_engine(
        name, key, mdl, free_tier=free_tier,
        base_url=settings_service.ollama_url()
        if name == "ollama" else None) for name, key, mdl, free_tier in built]
    if len(engines) > 1:
        engine = engine_fallback.FallbackEngine(engines, [c["engine"] for c in chain], caps)
        return engines, engine, None  # each engine's own cap is enforced by the FallbackEngine
    return engines, with_slow_switch(engines[0]) if slow_switch else engines[0], caps[0]


# The owner's blind picks on a modern novel put Gemma second after DeepSeek, at about half
# DeepSeek's price, so a slow DeepSeek run moves to it.
SLOW_SWITCH_FALLBACK = ("ollama", "gemma4:31b-cloud")


def with_slow_switch(engine):
    """A DeepSeek run's engine wrapped so the run moves to SLOW_SWITCH_FALLBACK while
    DeepSeek is slow (engine_fallback.SlowSwitchEngine); any other engine unchanged."""
    if getattr(engine, "name", "") != "deepseek" or isinstance(engine, engine_fallback.FallbackEngine):
        return engine
    if local.member_cloud_key() == local.UNREADABLE_MEMBER_KEY:
        return engine  # the member's hosted runs are refused until they paste the key again
    from services import translate_service
    name, model = SLOW_SWITCH_FALLBACK
    fallback = engine_registry.get_engine(name, translate_service.resolve_api_key(name), model,
                                          base_url=settings_service.ollama_url())
    return engine_fallback.SlowSwitchEngine([engine, fallback], ["deepseek", name])


def require_no_pending_bulk_overlap(drama_id, eligible) -> None:
    """A live run over lines a paid bulk batch is still translating would turn
    the batch into a charge for nothing: its results are discarded for every
    line whose English changed since submission."""
    ours = {ln.id for ln in eligible}
    for job in db.list_bulk_jobs(drama_id, statuses=BULK_IN_FLIGHT_STATUSES):
        if (job.get("kind") or "translate") not in ("translate", "reflect"):
            continue
        if ours & {r["line_id"] for r in db.list_bulk_job_lines(job["id"])}:
            raise ConflictError(
                "A bulk translation is still pending for some of these lines. "
                "Wait for it to finish, or cancel it first.")


def require_bulk_ready(drama_id, engine_name, model, main_cap, reflect, force_retranslate,
                       gemini_free_tier, job_cost_cap_usd, estimate_cost, style_preset=None):
    """The batch's estimated dollars, which it holds of the monthly cap until it
    is billed, or None when it holds none (DeepSeek off-peak, or no cap)."""
    # A batch or an off-peak run is paid for with nobody watching.
    paid_job_service.require_cap_for_unattended(engine_name, gemini_free_tier, model)
    if db.list_bulk_jobs(drama_id, statuses=db.BULK_PENDING_STATUSES + ("running",)):
        raise ConflictError("A bulk translation is already pending for this title.")
    if engine_name == "deepseek" or main_cap is None:
        # DeepSeek off-peak runs as a normal run later and stops at the
        # cap; a submitted batch can't, so it's refused up front.
        return None
    est = estimate_cost(
        drama_id, engine_name, model, reflect=reflect, bulk=True,
        force_retranslate=force_retranslate, gemini_free_tier=gemini_free_tier,
        job_cost_cap_usd=job_cost_cap_usd, style_preset=style_preset)
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            "Not submitted: a bulk batch can't be stopped part-way, and its estimate "
            "is above what's left of your cap once pending bulk jobs are counted. "
            "Raise the cap, or run a normal translation.")
    return est["estimated_usd"]


DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
_BALANCE_CACHE_SECONDS = 60
_balance_cache: dict = {}
_clock = time.monotonic


def deepseek_balance_usd():
    """DeepSeek's prepaid balance in US dollars, or None when it can't be read (no key, no
    answer, a balance kept in another currency). Asked at most once a minute per key, since
    the estimate is asked again on every change to the Translate form."""
    from services import translate_service
    key = translate_service.resolve_api_key("deepseek")
    if not key:
        return None
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    cached = _balance_cache.get(fingerprint)
    if cached and _clock() - cached[0] < _BALANCE_CACHE_SECONDS:
        return cached[1]
    balance = _read_deepseek_balance(key)
    _balance_cache.clear()
    _balance_cache[fingerprint] = (_clock(), balance)
    return balance


def _read_deepseek_balance(key: str):
    try:
        resp = http.get(DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {key}",
                                                       "Accept": "application/json"},
                        timeout=(3, 5), max_bytes=64 * 1024)
        data = json.loads(resp.text()) if resp.ok else None
    except Exception:
        return None  # a warning only: the run itself still finds out
    if not isinstance(data, dict):
        return None
    if data.get("is_available") is False:
        return 0.0
    for info in data.get("balance_infos") or []:
        if isinstance(info, dict) and info.get("currency") == "USD":
            try:
                return float(info.get("total_balance", ""))
            except (TypeError, ValueError):
                return None
    return None


def balance_too_low(engine_name: str, estimated_usd) -> bool:
    """Whether DeepSeek's balance looks too small for a run estimated at `estimated_usd`."""
    if engine_name != "deepseek" or not estimated_usd:
        return False
    balance = deepseek_balance_usd()
    return balance is not None and balance < estimated_usd
