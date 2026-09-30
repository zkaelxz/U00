"""
services/metadata_research_service.py -- "Research online" for a drama's
metadata (roadmap Step 37): Gemini with Google Search grounding, returning
per-field values with their own cited sources, for the user to review before
anything is written.

  - budget_status: today's grounded-search count against the free daily
    allowance, plus the monthly spending cap and this month's spend.
  - research: one grounded lookup (quick / deep / verify) for the drama's
    title. Results are cached by the looked-up entity, so a repeat lookup
    never re-spends the budget. Writes nothing to the drama.
  - apply_research: writes only the fields the user chose ("replace"), and
    records each accepted or kept-beside ("save_both") value with its own
    provenance (value, source, source_url, retrieved_at, confidence,
    last_verified). "keep" writes nothing. A field without a choice is never
    written, so a researched value never silently overwrites one.
  - list_provenance: the stored per-field evidence for a drama.

Safety and cost: the Gemini key is resolved server-side and sent as a header,
never in a URL, response or error (fixed messages only). The model is the one
the caller picked; there is no automatic model switching. Once today's free
allowance is used, a lookup is refused unless the caller opts into paid
searches, which also needs a paid (non-free-tier) key and room under the
monthly cap. Values are matched to fields by name, never by list position.
Cited source URLs are kept only when they are http(s).

No Streamlit or FastAPI import: plain dicts in, plain dicts out.
"""
import datetime
import hashlib
import json
import logging
import re
import threading
from typing import Optional
from urllib.parse import urlsplit

import db
import translate_engines
from services import drama_service, library_service, settings_service
from services.metadata_service import SUGGEST_FIELDS, _require_drama
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

log = logging.getLogger(__name__)

RESEARCH_FIELDS = SUGGEST_FIELDS
MODES = ("quick", "deep", "verify")
# The user picks the model; a lookup never switches to another one on its own.
MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")
DEFAULT_MODEL = MODELS[0]
CHOICES = ("keep", "replace", "save_both", "confirm")

# Google Search grounding (ai.google.dev pricing, checked 2026-09-30): the
# 2.5 Flash models allow 500 grounded prompts/day free, then $35 / 1,000; the
# 3.x Flash models allow 5,000 search queries/month free (shared), then $14 /
# 1,000. The "-latest" aliases don't say which generation they are, so both
# limits apply at once (whichever runs out first), every search query the
# model ran counts, and a paid search is priced at the higher rate. Google's
# day starts at midnight Pacific; the counter's day starts at 08:00 UTC,
# which is never earlier than Google's, so it never shows more left than
# there is.
GROUNDED_FREE_RPD = 500
GROUNDED_FREE_MONTHLY = 5000
GROUNDED_PAID_PRICE_USD = 0.035
_DAY_OFFSET = datetime.timedelta(hours=8)
# Rough per-lookup token sizes, only for the up-front cost estimate.
EST_INPUT_TOKENS = {"quick": 800, "deep": 1200, "verify": 1200}
EST_OUTPUT_TOKENS = {"quick": 800, "deep": 2000, "verify": 1000}
MAX_RELATED = 10
MAX_SOURCES = 20
REQUEST_TIMEOUT = 60
BUDGET_SETTING = "grounded_search_usage"
_RESEARCH_ID = re.compile(r"^[0-9a-f]{64}$")
_SELF_CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
_FIELD_LABELS = {"title_en": "English title", "title_zh": "original-language title",
                 "author": "original author (novel/manga, if any)",
                 "studio": "production studio or publisher", "director": "director",
                 "voice_actors": "main cast / voice actors (comma-separated)",
                 "summary": "a short plot synopsis (2-4 sentences, English)"}
_UNAVAILABLE = "The online research service is unavailable. Try again later."


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _today() -> str:
    return (_now() - _DAY_OFFSET).date().isoformat()


_budget_lock = threading.Lock()


def _usage() -> tuple:
    """(searches today, searches this month) on Google's calendar."""
    saved = db.get_app_setting(BUDGET_SETTING) or {}
    today = _today()
    try:
        day = max(0, int(saved.get("count") or 0)) if saved.get("date") == today else 0
        month = max(0, int(saved.get("month_count") or 0)) \
            if saved.get("month") == today[:7] else 0
    except (TypeError, ValueError, AttributeError):
        return 0, 0
    return day, month


def _free_remaining(day: int, month: int) -> int:
    return max(0, min(GROUNDED_FREE_RPD - day, GROUNDED_FREE_MONTHLY - month))


def _count_searches(n: int = 1) -> int:
    """Adds n searches; returns how many were free before adding. Held under
    a lock so two lookups at once can't both take the last free search."""
    with _budget_lock:
        day, month = _usage()
        today = _today()
        db.set_app_setting(BUDGET_SETTING, {"date": today, "count": day + n,
                                            "month": today[:7], "month_count": month + n})
        return _free_remaining(day, month)


def budget_status() -> dict:
    day, month = _usage()
    cap = settings_service.get_monthly_cap_usd()
    return {"free_daily_limit": GROUNDED_FREE_RPD, "used_today": day,
            "free_monthly_limit": GROUNDED_FREE_MONTHLY, "used_this_month": month,
            "free_remaining": _free_remaining(day, month),
            "paid_price_per_search_usd": GROUNDED_PAID_PRICE_USD,
            "free_tier_key": settings_service.get_gemini_free_tier(),
            "key_configured": bool(settings_service.resolve_key("gemini")),
            "monthly_cap_usd": cap, "month_spend_usd": round(db.get_month_spend(), 4),
            "models": list(MODELS), "modes": list(MODES),
            "estimates_usd": {m: {mo: round(estimate_cost(m, mo, False), 4) for mo in MODELS}
                              for m in MODES}}


def _entity(drama: dict, mode: str) -> dict:
    ent = {k: (drama.get(k) or "").strip() for k in ("title_zh", "title_en", "author")}
    ent["source_language"] = drama.get("source_language") or ""
    ent["media_type"] = drama.get("media_type") or ""
    if mode == "verify":
        ent["current"] = {k: (drama.get(k) or "").strip() for k in RESEARCH_FIELDS}
    return ent


def _cache_key(entity: dict, mode: str, model: str) -> str:
    raw = json.dumps({"v": 1, "mode": mode, "model": model, "entity": entity},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prompt(entity: dict, mode: str) -> str:
    known = {k: v for k, v in entity.items() if k != "current" and v}
    wanted = "\n".join(f'- "{k}": {_FIELD_LABELS[k]}' for k in RESEARCH_FIELDS)
    lines = [
        "Use Google Search to research this drama/anime/novel adaptation and report "
        "bibliographic facts found in reliable sources (official sites, publishers, "
        "encyclopedias, major databases).",
        f"What is already known: {json.dumps(known, ensure_ascii=False)}",
        f"Fields:\n{wanted}",
    ]
    if mode == "deep":
        lines.append("Check several independent sources, prefer official ones, and fill "
                     "as many fields as the sources support.")
    elif mode == "verify":
        lines.append("Verify these existing values against the sources and give the correct "
                     "value for each (the same value when it is already right): "
                     + json.dumps(entity.get("current") or {}, ensure_ascii=False))
    else:
        lines.append("A quick identity check: confirm the titles and the main credits.")
    lines.append(
        'Reply with JSON only, no prose: {"fields": {"<field>": {"value": "...", '
        '"confidence": "high|medium|low"}}, "related": [{"title": "...", "relation": '
        '"e.g. manga adaptation"}]}. Leave out any field the sources do not support; '
        "never guess. \"related\" lists works the sources mention as directly related "
        "(adaptations, sequels, originals).")
    return "\n\n".join(lines)


def _call_gemini(api_key: str, model: str, prompt: str) -> dict:
    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(url, headers={"x-goog-api-key": api_key},
                         json={"contents": [{"parts": [{"text": prompt}]}],
                               "tools": [{"google_search": {}}]},
                         timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _safe_url(url) -> Optional[str]:
    if not isinstance(url, str) or len(url) > drama_service.MAX_URL_LEN:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return url


def _clip(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _parse_response(data: dict) -> dict:
    """{"fields": {field: {"value", "sources", "confidence"}}, "sources",
    "related"} from a grounded generateContent response. Each field's sources
    come from the grounding supports whose text contains that field's value."""
    candidates = data.get("candidates") or []
    if not candidates:
        return {"fields": {}, "sources": [], "related": [], "search_queries": []}
    cand = candidates[0] or {}
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    meta = cand.get("groundingMetadata") or {}

    chunks = []  # index-aligned with groundingChunks; None for an unusable one
    for ch in (meta.get("groundingChunks") or [])[:MAX_SOURCES * 5]:
        web = (ch or {}).get("web") or {}
        url = _safe_url(web.get("uri"))
        chunks.append({"title": _clip(web.get("title"), 200) or urlsplit(url).hostname,
                       "url": url} if url else None)
    supports = []
    for sup in meta.get("groundingSupports") or []:
        seg = _clip(((sup or {}).get("segment") or {}).get("text"), 20_000)
        idx = [i for i in (sup.get("groundingChunkIndices") or [])
               if isinstance(i, int) and 0 <= i < len(chunks) and chunks[i]]
        scores = [s for s in (sup.get("confidenceScores") or []) if isinstance(s, (int, float))]
        if seg and idx:
            supports.append((seg, idx, max(scores) if scores else None))

    parsed = _parse_json(text)
    raw_fields = parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {}
    fields = {}
    for key in RESEARCH_FIELDS:  # by name; anything else the model sent is ignored
        item = raw_fields.get(key)
        if isinstance(item, str):
            item = {"value": item}
        if not isinstance(item, dict):
            continue
        limit = drama_service.MAX_LONG_TEXT_LEN if key == "summary" else drama_service.MAX_NAME_LEN
        value = _clip(item.get("value"), limit)
        if not value:
            continue
        used, score = [], None
        for seg, idx, s in supports:
            # A segment that names other fields' keys but not this one's is
            # about them (e.g. an author name repeated inside the summary).
            keys = [k for k in RESEARCH_FIELDS if f'"{k}"' in seg]
            if keys and key not in keys:
                continue
            if value in seg or (len(seg) >= 20 and seg in value):
                used.extend(i for i in idx if i not in used)
                if s is not None:
                    score = s if score is None else max(score, s)
        if score is None:
            score = _SELF_CONFIDENCE.get(str(item.get("confidence") or "").lower())
        fields[key] = {"value": value, "sources": [chunks[i] for i in used][:MAX_SOURCES],
                       "confidence": round(float(score), 3) if score is not None else None}

    seen, sources = set(), []
    for ch in chunks:
        if ch and ch["url"] not in seen and len(sources) < MAX_SOURCES:
            seen.add(ch["url"])
            sources.append(ch)
    related = []
    for rel in parsed.get("related") or []:
        if isinstance(rel, dict) and _clip(rel.get("title"), 300):
            related.append({"title": _clip(rel.get("title"), 300),
                            "relation": _clip(rel.get("relation"), 100)})
        if len(related) >= MAX_RELATED:
            break
    # Google's grounding terms ask for the search suggestions to be shown;
    # the app shows the queries as links to a Google search.
    queries = [_clip(q, 200) for q in (meta.get("webSearchQueries") or [])
               if isinstance(q, str) and q.strip()][:MAX_RELATED]
    return {"fields": fields, "sources": sources, "related": related, "search_queries": queries}


def _rows(result: dict, drama: dict) -> list:
    rows = []
    for key in RESEARCH_FIELDS:
        item = (result.get("fields") or {}).get(key)
        if not item:
            continue
        current = (drama.get(key) or "").strip()
        status = "new" if not current else ("same" if current == item["value"] else "conflict")
        rows.append({"field": key, "value": item["value"], "current": current or None,
                     "status": status, "sources": item.get("sources") or [],
                     "confidence": item.get("confidence")})
    return rows


def _response(drama_id: int, drama: dict, key: str, result: dict, cached: bool) -> dict:
    return {"drama_id": drama_id, "research_id": key, "cached": cached,
            "mode": result["mode"], "model": result["model"],
            "retrieved_at": result["retrieved_at"], "fields": _rows(result, drama),
            "sources": result.get("sources") or [], "related": result.get("related") or [],
            "search_queries": result.get("search_queries") or [],
            "cost_usd": 0.0 if cached else result.get("cost_usd", 0.0),
            "budget": budget_status()}


def estimate_cost(mode: str, model: str, paid_search: bool) -> float:
    if settings_service.get_gemini_free_tier():
        return 0.0
    cost = translate_engines.estimate_cost(model, EST_INPUT_TOKENS[mode], EST_OUTPUT_TOKENS[mode])
    return cost + (GROUNDED_PAID_PRICE_USD if paid_search else 0.0)


def _refuse_paid(allow_paid: bool, free_tier: bool):
    if not allow_paid:
        raise ConflictError(
            "The free online searches are used up (they reset daily, and there is also a "
            f"monthly limit). Allow paid searches (about ${GROUNDED_PAID_PRICE_USD:.3f} each) "
            "or try again later.", details={"reason": "free_budget_used"})
    if free_tier:
        raise ConflictError("A free-tier Gemini key cannot run paid searches.",
                            details={"reason": "free_tier_key"})


def _check_cap(mode: str, model: str, paid_search: bool):
    estimate = estimate_cost(mode, model, paid_search)
    cap = settings_service.get_monthly_cap_usd()
    if cap and estimate > 0:
        spend = db.get_month_spend()
        if spend + estimate > cap:
            raise ConflictError(
                f"This lookup (about ${estimate:.3f}) would pass this month's spending cap "
                f"(${cap:.2f}, ${spend:.2f} used). Raise it in Settings.",
                details={"reason": "monthly_cap"})


def _search_query_count(data: dict) -> int:
    try:
        meta = (data.get("candidates") or [{}])[0].get("groundingMetadata") or {}
        return len(meta.get("webSearchQueries") or [])
    except (AttributeError, IndexError, TypeError):
        return 0


def research(drama_id: int, mode: str = "quick", model: Optional[str] = None,
             allow_paid: bool = False, refresh: bool = False) -> dict:
    """One grounded lookup; writes nothing to the drama. A repeat of the same
    lookup comes from the cache (no search used) unless `refresh` asks for a
    new one. Unknown drama 404;
    bad mode/model or no title 422; free allowance used up without
    allow_paid, a paid search on a free-tier key, or the monthly cap 409;
    no key / API failure 503 (fixed text)."""
    drama = _require_drama(drama_id)
    if mode not in MODES:
        raise InvalidInputError("Unknown research mode.", details={"allowed": list(MODES)})
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise InvalidInputError("That model cannot run grounded research.",
                                details={"allowed": list(MODELS)})
    entity = _entity(drama, mode)
    if not (entity["title_zh"] or entity["title_en"]):
        raise InvalidInputError("Add a title first; research looks the title up.")
    key = _cache_key(entity, mode, model)
    cached = None if refresh else db.get_research_cache(key)
    if cached:
        return _response(drama_id, drama, key, cached, cached=True)

    api_key = settings_service.resolve_key("gemini")
    if not api_key:
        raise DependencyUnavailableError("No Gemini key is configured. Set one in Settings first.")
    free_tier = settings_service.get_gemini_free_tier()
    paid_search = _free_remaining(*_usage()) <= 0
    if paid_search:
        _refuse_paid(allow_paid, free_tier)
    _check_cap(mode, model, paid_search)
    # Take the search before the call (under a lock), so two lookups at once
    # can't both use the last free one; a failed call still counts, since
    # Google may have run the search anyway.
    paid_search = _count_searches(1) <= 0
    if paid_search:
        _refuse_paid(allow_paid, free_tier)
    try:
        data = _call_gemini(api_key, model, _prompt(entity, mode))
    except Exception as e:  # never echo: the text could carry request details
        log.warning("Grounded research failed: %s",
                    translate_engines.redact_secrets(type(e).__name__))
        raise DependencyUnavailableError(_UNAVAILABLE) from None
    extra = max(0, _search_query_count(data) - 1)
    paid_extra = 0
    if extra:  # the 3.x models bill every query the model ran
        free_left = _count_searches(extra)
        paid_extra = max(0, extra - free_left)
    usage = translate_engines.gemini_usage(data.get("usageMetadata"))
    cost = 0.0
    if not free_tier:
        cost = translate_engines.estimate_cost(model, usage["input_tokens"],
                                               usage["output_tokens"])
        cost += GROUNDED_PAID_PRICE_USD * ((1 if paid_search else 0) + paid_extra)
    db.log_usage(drama_id, "gemini", model, "metadata_research", usage["input_tokens"],
                 usage["output_tokens"], cost)
    try:
        parsed = _parse_response(data)
    except Exception:
        log.warning("Grounded research returned an unreadable response.")
        raise DependencyUnavailableError(_UNAVAILABLE) from None
    result = {"mode": mode, "model": model, "retrieved_at": _now().isoformat() + "Z",
              "cost_usd": round(cost, 6), **parsed}
    if parsed["fields"]:  # an empty answer is not cached, so it can be retried
        db.put_research_cache(key, result)
    return _response(drama_id, drama, key, result, cached=False)


def apply_research(drama_id: int, research_id: str, choices: dict, seen: Optional[dict] = None,
                   principal=None) -> dict:
    """choices: {field: "keep" | "replace" | "save_both" | "confirm"}. Values
    come from the cached research result, never from the client. `seen`
    holds, per chosen field, the drama's value the user was shown next to the
    research; if it has changed since (an edit in Details, another user),
    nothing is written (409), so a value the user never saw is never
    replaced. "confirm" records the sources for a value that already matches.
    Returns
    {"drama_id", "replaced", "saved_alternates", "kept", "drama"}."""
    drama = _require_drama(drama_id)
    if not isinstance(research_id, str) or not _RESEARCH_ID.match(research_id):
        raise InvalidInputError("research_id is not valid.")
    if not isinstance(choices, dict) or not choices:
        raise InvalidInputError("Choose Keep, Replace or Save both for at least one field.")
    result = db.get_research_cache(research_id)
    if not result:
        raise NotFoundError("That research result is no longer available. Run it again.")
    fields = result.get("fields") or {}
    for field, choice in choices.items():
        if field not in fields:
            raise InvalidInputError(f"{field} is not in this research result.",
                                    details={"allowed": sorted(fields)})
        if choice not in CHOICES:
            raise InvalidInputError("Each choice must be keep, replace, save_both or confirm.",
                                    details={"allowed": list(CHOICES)})
        if choice == "keep":
            continue
        current = (drama.get(field) or "").strip()
        if not isinstance(seen, dict) or field not in seen:
            raise InvalidInputError(f"Send the {field} value you were shown.")
        if (seen[field] or "").strip() != current:
            raise ConflictError("This drama's details changed since the research was shown. "
                                "Look again before applying.", details={"reason": "changed",
                                                                       "field": field})
        if choice == "confirm" and fields[field]["value"] != current:
            raise InvalidInputError(f"{field} does not match the research; choose another option.")
    replace = {f: fields[f]["value"] for f, c in choices.items() if c == "replace"}
    if replace:
        detail = drama_service.update_drama_metadata(drama_id, principal=principal, **replace)
    else:
        detail = None
    saved, confirmed = [], []
    for field, choice in choices.items():
        if choice == "keep":
            continue
        item = fields[field]
        if choice == "save_both" and item["value"] == (drama.get(field) or "").strip():
            continue  # nothing to keep beside: it is the same value
        status = {"replace": "applied", "save_both": "alternate", "confirm": "verified"}[choice]
        # last_verified is when the sources were checked (the lookup), not
        # when the user clicked Apply on a possibly older cached result.
        db.add_field_provenance(
            drama_id, field, item["value"], status,
            sources=item.get("sources"), retrieved_at=result.get("retrieved_at"),
            confidence=item.get("confidence"), last_verified=result.get("retrieved_at"))
        if choice == "confirm":
            confirmed.append(field)
        if choice == "save_both":
            saved.append(field)
    if detail is None:
        detail = library_service.get_library_drama(drama_id)
    return {"drama_id": drama_id, "replaced": sorted(replace), "saved_alternates": saved,
            "confirmed": confirmed,
            "kept": sorted(f for f, c in choices.items() if c == "keep"), "drama": detail}


def list_provenance(drama_id: int) -> dict:
    _require_drama(drama_id)
    return {"drama_id": drama_id, "fields": db.list_field_provenance(drama_id)}
