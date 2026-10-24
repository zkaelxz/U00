"""
services/metadata_research_service.py -- "Research online" for a drama's
metadata: Gemini with Google Search grounding, returning
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

No FastAPI import: plain dicts in, plain dicts out.
"""
import datetime
import hashlib
import json
import logging
import re
import secrets
import threading
from typing import Optional
from urllib.parse import urlsplit

import db
import translate_engines
from services import drama_service, library_service, settings_service
from lib import http
from services.metadata_service import SUGGEST_FIELDS, require_drama
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, MissingKeyError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

RESEARCH_FIELDS = SUGGEST_FIELDS
MODES = ("quick", "deep", "verify")
# Google Search grounding allowance (ai.google.dev pricing, checked
# 2026-10-24), shared by every model in MODELS, so the counter
# keeps one total: grounded prompts per day, 500 free on a free-tier key and
# 1,500 free on a paid key, then $35 / 1,000. Google documents the 5,000 a
# month only for 3.x models; it stays as a ceiling so the counter never
# shows more left than there is. A 3.x model would need its own allowance
# and total.
GROUNDING_ALLOWANCE = {"free_key_daily": 500, "paid_key_daily": 1500, "monthly": 5000}
# The user picks the model; a lookup never switches to another one on its own.
# Pinned to 2.5, not the "-latest" aliases: the free tier gives 3.x models no
# Google Search grounding, and then a lookup fails.
MODELS = ("gemini-2.5-flash-lite", "gemini-2.5-flash")
DEFAULT_MODEL = MODELS[0]
CHOICES = ("keep", "replace", "save_both", "confirm")

# Every search query the model ran counts against the allowance and a paid search
# is priced at the higher (2.5) rate, so the counter never undercounts.
# Google's day starts at midnight Pacific; the counter's day starts at 08:00
# UTC, which is never earlier than Google's, so it never shows more left
# than there is.
GROUNDED_PAID_PRICE_USD = 0.035
# A learned limit outside this range is not believed.
_MAX_LEARNED_RPD = 1_000_000
# A grounded prompt may run several search queries (each billed on 3.x). A
# lookup only counts as free while at least this many free searches are left;
# otherwise it is a paid lookup (needs allow_paid) and the cap check assumes
# this many paid queries.
MAX_QUERIES_PER_LOOKUP = 5
_DAY_OFFSET = datetime.timedelta(hours=8)
# Rough per-lookup token sizes, only for the up-front cost estimate.
EST_INPUT_TOKENS = {"quick": 800, "deep": 1200, "verify": 1200}
EST_OUTPUT_TOKENS = {"quick": 800, "deep": 2000, "verify": 1000}
MAX_RELATED = 10
MAX_SOURCES = 20
REQUEST_TIMEOUT = 60
MAX_RESPONSE_BYTES = 4_000_000
BUDGET_SETTING = "grounded_search_usage"
_RESEARCH_ID = re.compile(r"^[0-9a-f]{64}$")
_SELF_CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
_FIELD_LABELS = {"title_en": "English title", "title_zh": "original-language title",
                 "author": "original author (novel/manga, if any)",
                 "studio": "production studio or publisher", "director": "director",
                 "voice_actors": "main cast / voice actors (comma-separated)",
                 "summary": "a short plot synopsis (2-4 sentences, English)"}
_UNAVAILABLE = "The online research service is unavailable. Try again later."
# finishReason values where Gemini withheld its answer on policy grounds.
_BLOCK_REASONS = translate_engines.GEMINI_BLOCK_REASONS



def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _today() -> str:
    return (_now() - _DAY_OFFSET).date().isoformat()


_budget_lock = threading.Lock()


def _whole(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _usage() -> dict:
    """The counter on Google's calendar: searches today ("count") and this
    month, each model's requests today, the models Google said are used up
    today, and the daily limits Google named for them today. A named limit
    is not kept past the day: Google may raise it, and the key may change."""
    saved = db.get_app_setting(BUDGET_SETTING)
    saved = saved if isinstance(saved, dict) else {}
    today = _today()
    same_day = saved.get("date") == today
    models = saved.get("models") if same_day and isinstance(saved.get("models"), dict) else {}
    used_up = saved.get("used_up") if same_day and isinstance(saved.get("used_up"), list) else []
    learned = saved.get("learned_rpd") if same_day and isinstance(saved.get("learned_rpd"), dict) \
        else {}
    return {"date": today, "count": _whole(saved.get("count")) if same_day else 0,
            "month": today[:7],
            "month_count": _whole(saved.get("month_count"))
            if saved.get("month") == today[:7] else 0,
            "models": {m: _whole(models.get(m)) for m in MODELS if m in models},
            "used_up": [m for m in MODELS if m in used_up],
            "learned_rpd": {m: n for m in MODELS
                            if 0 < (n := _whole(learned.get(m))) <= _MAX_LEARNED_RPD}}


def _model_daily_limit(state: dict, model: str) -> int:
    """The model's own requests a day on a free-tier key, which every call
    from the key's project spends (translation too). Google shows the
    project's real figure only in AI Studio, so the engine table's usual
    figure stands until a daily-quota reply names it."""
    return (state["learned_rpd"].get(model)
            or translate_engines.gemini_free_tier_limits_for(model)["rpd"])


def _daily_limit(free_tier: bool) -> int:
    return GROUNDING_ALLOWANCE["free_key_daily" if free_tier else "paid_key_daily"]


def _free_remaining(state: dict, model: str, free_tier: bool) -> int:
    if model in state["used_up"]:
        return 0
    left = min(_daily_limit(free_tier) - state["count"],
               GROUNDING_ALLOWANCE["monthly"] - state["month_count"])
    # A paid key's own per-model limits are far above the grounding allowance.
    if free_tier:
        left = min(left, _model_daily_limit(state, model) - state["models"].get(model, 0))
    return max(0, left)


def _runs_free(state: dict, model: str, free_tier: bool) -> bool:
    """Whether a lookup on `model` is a free one: the shared allowance has
    room for the most searches one lookup may run, and (on a free-tier key)
    the model has a request left, since a lookup is one request."""
    if model in state["used_up"]:
        return False
    searches_left = min(_daily_limit(free_tier) - state["count"],
                        GROUNDING_ALLOWANCE["monthly"] - state["month_count"])
    if searches_left < MAX_QUERIES_PER_LOOKUP:
        return False
    return not free_tier or \
        _model_daily_limit(state, model) - state["models"].get(model, 0) >= 1


def _add(state: dict, model: Optional[str], searches: int, requests: int):
    state["count"] = max(0, state["count"] + searches)
    state["month_count"] = max(0, state["month_count"] + searches)
    if model:
        state["models"][model] = max(0, state["models"].get(model, 0) + requests)


def _save(state: dict):
    db.set_app_setting(BUDGET_SETTING, state)


def _count_searches(n: int = 1):
    """Adds n searches to the shared allowance (not to any model's own
    requests: one call is one request however many searches it ran)."""
    with _budget_lock:
        state = _usage()
        _add(state, None, n, 0)
        _save(state)


def _model_budget(state: dict, model: str, free_tier: bool) -> dict:
    return {"free_daily_limit": min(_daily_limit(free_tier), _model_daily_limit(state, model))
            if free_tier else _daily_limit(free_tier),
            "requests_today": state["models"].get(model, 0),
            "free_remaining": _free_remaining(state, model, free_tier),
            "runs_free": _runs_free(state, model, free_tier),
            "used_up": model in state["used_up"]}


def budget_status(model: Optional[str] = None) -> dict:
    """The top-level counts are for `model` (the default model when None);
    "per_model" has each model's own."""
    model = model if model in MODELS else DEFAULT_MODEL
    state = _usage()
    free_tier = bool(settings_service.get("gemini_free_tier"))
    cap = settings_service.get_monthly_cap_usd()
    return {"free_daily_limit": _model_budget(state, model, free_tier)["free_daily_limit"],
            "used_today": state["count"],
            "free_monthly_limit": GROUNDING_ALLOWANCE["monthly"],
            "used_this_month": state["month_count"],
            "free_remaining": _free_remaining(state, model, free_tier),
            "per_model": {m: _model_budget(state, m, free_tier) for m in MODELS},
            "paid_price_per_search_usd": GROUNDED_PAID_PRICE_USD,
            "free_lookup_min": MAX_QUERIES_PER_LOOKUP,
            "free_tier_key": free_tier,
            "key_configured": bool(settings_service.resolve_key("gemini")),
            "monthly_cap_usd": cap,
            # Counted since any reset under a cap; the real spend with none.
            "month_spend_usd": round(db.get_month_spend(since_reset=cap > 0), 4),
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
    url, headers = translate_engines.gemini_generate_request(model, api_key)
    resp = http.post(url, headers=headers,
                     json={"contents": [{"parts": [{"text": prompt}]}],
                           "tools": [{"google_search": {}}]},
                     timeout=REQUEST_TIMEOUT, max_bytes=MAX_RESPONSE_BYTES, guard=None)
    if not resp.ok:
        raise _GeminiRefused(resp.status, resp.body)
    data = json.loads(resp.body)
    if not isinstance(data, dict):
        raise http.FetchError()
    return data


def _withheld_reason(data: dict) -> Optional[ServiceError]:
    """Why a grounded reply carries no usable answer when Gemini itself
    says so (a safety block, or a reply cut off at the output limit);
    None when it does not say."""
    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        reason = "blocked"
    else:
        cands = data.get("candidates")
        cand = cands[0] if isinstance(cands, list) and cands else None
        finish = cand.get("finishReason") if isinstance(cand, dict) else None
        if finish in _BLOCK_REASONS:
            reason = "blocked"
        elif finish == "MAX_TOKENS":
            reason = "truncated"
        else:
            return None
    if reason == "blocked":
        return DependencyUnavailableError(
            "Gemini withheld its answer for this title (a safety filter).",
            details={"reason": "blocked", "engine": "Gemini"})
    return DependencyUnavailableError(
        "Gemini's answer was cut off before it finished. Try again.",
        details={"reason": "truncated", "engine": "Gemini"})


class _GeminiRefused(http.FetchError):
    """Gemini answered with an HTTP error; the status picks the message."""

    def __init__(self, status: int, body: bytes = b""):
        super().__init__()
        self.status = status
        try:
            self.detail = str((json.loads(body).get("error") or {}).get("message") or "")[:300]
        except (ValueError, AttributeError, TypeError):
            self.detail = ""
        # Google's status words, read only to pick the reason, never shown.
        text = (body or b"")[:20_000].decode("utf-8", "replace")
        self.daily_quota = _daily_quota(body) if status == 429 or "RESOURCE_EXHAUSTED" in text \
            else None
        if self.daily_quota is not None:
            self.reason = "searches_used_up" if self.daily_quota["searches"] else "model_used_up"
            self.message_status = self.reason
        elif status == 429 or "RESOURCE_EXHAUSTED" in text:
            self.reason, self.message_status = "rate_limited", 429
        elif status in (401, 403) or "API_KEY_INVALID" in text:
            self.reason, self.message_status = "key_rejected", 403
        elif status == 404:
            self.reason, self.message_status = "model_unavailable", 404
        elif 400 <= status < 500:
            self.reason, self.message_status = "refused", 400
        else:
            self.reason, self.message_status = "unavailable", None
        # A 4xx is refused before Google runs any search, so the reserved
        # search is given back; a 5xx may have searched.
        self.searched = not 400 <= status < 500

    def as_error(self) -> DependencyUnavailableError:
        return DependencyUnavailableError(
            _REFUSED_MESSAGES.get(self.message_status, _UNAVAILABLE),
            details={"reason": self.reason, "engine": "Gemini"})


def _daily_quota(body: bytes) -> Optional[dict]:
    """The per-day quota a RESOURCE_EXHAUSTED reply says ran out, from its
    QuotaFailure detail: {"searches": the Google Search allowance rather
    than the model's own requests, "model": the model it names, "limit":
    the free-tier limit it names, or 0}. None for a per-minute limit or a
    reply without that detail."""
    try:
        details = (json.loads(body).get("error") or {}).get("details") or []
    except (ValueError, AttributeError, TypeError):
        return None
    for detail in details if isinstance(details, list) else []:
        violations = detail.get("violations") if isinstance(detail, dict) else None
        for v in violations if isinstance(violations, list) else []:
            if not isinstance(v, dict):
                continue
            quota_id = str(v.get("quotaId") or "")
            if "PerDay" not in quota_id:
                continue
            metric = f'{quota_id} {v.get("quotaMetric") or ""}'.lower()
            dims = v.get("quotaDimensions") if isinstance(v.get("quotaDimensions"), dict) else {}
            return {"searches": "search" in metric or "ground" in metric,
                    "model": dims.get("model") if dims.get("model") in MODELS else None,
                    # Only a requests-a-day quota is a model's request limit
                    # (a tokens-a-day one still stops the model for the day).
                    "limit": _whole(v.get("quotaValue"))
                    if "FreeTier" in quota_id and "request" in metric else 0}
    return None


# By Gemini's status (or the daily quota that ran out); anything else is
# _UNAVAILABLE.
_REFUSED_MESSAGES = {
    "model_used_up": "Today's free Gemini requests for this model are used up. "
                     "Pick the other model, or try again tomorrow.",
    "searches_used_up": "Today's free Google searches through Gemini are used up. "
                        "Try again tomorrow.",
    400: "Gemini refused the request. Check the Gemini key in Settings.",
    401: "Gemini refused the key. Check the Gemini key in Settings.",
    403: "Gemini refused the key. Check the Gemini key in Settings.",
    404: "This Gemini key cannot use that model. Pick the other model.",
    429: "Gemini's rate limit was reached. Try again later.",
}


def _safe_url(url) -> Optional[str]:
    """An http(s) source link as scheme, host and path: a link can carry
    credentials, a signed query or a fragment, and the API never shows them.
    Unlike display_url the path is kept whatever it looks like: Gemini's own
    grounding links are redirects whose opaque path is the whole link."""
    if not isinstance(url, str) or len(url) > drama_service.MAX_URL_LEN:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}{port}{parts.path.split(';', 1)[0]}"


def _clip(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _field_text(value) -> object:
    """A cast or credits field often comes back as a list of names."""
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return ", ".join(v.strip() for v in value if v.strip())
    return value


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
        value = _clip(_field_text(item.get("value")), limit)
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


def _snapshot(drama_id: int, result: dict) -> str:
    # A fresh id per lookup shown, stored with this drama and a snapshot of
    # exactly what was shown: a later "Look up again" (another tab, another
    # user) rewrites the entity cache but never what this id applies.
    research_id = secrets.token_hex(32)
    db.put_research_result(research_id, drama_id, result)
    return research_id


def _response(drama_id: int, drama: dict, key: str, result: dict, cached: bool,
              snapshot: bool = True) -> dict:
    research_id = _snapshot(drama_id, result) if snapshot else None
    return {"drama_id": drama_id, "research_id": research_id, "cached": cached,
            "mode": result["mode"], "model": result["model"],
            "retrieved_at": result["retrieved_at"], "fields": _rows(result, drama),
            "sources": result.get("sources") or [], "related": result.get("related") or [],
            "search_queries": result.get("search_queries") or [],
            "cost_usd": 0.0 if cached else result.get("cost_usd", 0.0),
            "budget": budget_status()}


def cached_research(drama_id: int, mode: str = "quick", model: Optional[str] = None) -> dict:
    """The result an earlier identical lookup left in the cache, shaped like
    `research`, so a page left and reopened shows it again without spending a
    search. NotFoundError when there is none (never looked up, or the title,
    mode or model changed since). Never calls Gemini, and writes nothing:
    `research_id` is None, and Apply snapshots the cache entry itself."""
    drama = require_drama(drama_id)
    if mode not in MODES:
        raise InvalidInputError("Unknown research mode.", details={"allowed": list(MODES)})
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise InvalidInputError("That model cannot run grounded research.",
                                details={"allowed": list(MODELS)})
    key = _cache_key(_entity(drama, mode), mode, model)
    cached = db.get_research_cache(key)
    if not cached:
        raise NotFoundError("No earlier lookup for this title with these settings.")
    return _response(drama_id, drama, key, cached, cached=True, snapshot=False)


def _snapshot_cached(drama_id: int, drama: dict, mode: Optional[str], model: Optional[str],
                     retrieved_at: Optional[str]) -> str:
    """Apply for a result shown by `cached_research`: snapshot the cache entry
    now, but only if it is still the lookup the person saw."""
    if mode not in MODES or model not in MODELS or not retrieved_at:
        raise InvalidInputError("Send research_id, or the mode, model and retrieved_at you were shown.")
    cached = db.get_research_cache(_cache_key(_entity(drama, mode), mode, model))
    if not cached:
        raise NotFoundError("That research result is no longer available. Run it again.")
    if cached.get("retrieved_at") != retrieved_at:
        raise ConflictError("This lookup was refreshed since it was shown. Look again before applying.",
                            details={"reason": "refreshed"})
    return _snapshot(drama_id, cached)


def estimate_cost(mode: str, model: str, paid_search: bool) -> float:
    if settings_service.get("gemini_free_tier"):
        return 0.0
    cost = translate_engines.estimate_cost(model, EST_INPUT_TOKENS[mode], EST_OUTPUT_TOKENS[mode])
    # Worst case: every query the model may run is a paid one.
    return cost + (GROUNDED_PAID_PRICE_USD * MAX_QUERIES_PER_LOOKUP if paid_search else 0.0)


def _refuse_paid(allow_paid: bool, free_tier: bool, state: dict, model: str):
    # Google refused this model for the day, so paying would only repeat that.
    if model in state["used_up"] or (
            free_tier and any(_runs_free(state, m, True) for m in MODELS if m != model)):
        raise ConflictError(_REFUSED_MESSAGES["model_used_up"],
                            details={"reason": "model_used_up"})
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
    drama = require_drama(drama_id)
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
        raise MissingKeyError("Gemini")
    free_tier = settings_service.get("gemini_free_tier")
    paid_search, free_before, reserved_on = _reserve_search(mode, model, allow_paid, free_tier)
    try:
        data = _call_gemini(api_key, model, _prompt(entity, mode))
    except Exception as e:  # never echo: the text could carry request details
        status = e.status if isinstance(e, _GeminiRefused) else None
        detail = e.detail if isinstance(e, _GeminiRefused) else ""
        log.warning("Grounded research failed: %s%s%s", type(e).__name__,
                    f" (HTTP {status})" if status else "",
                    f": {translate_engines.redact_secrets(detail)}" if detail else "")
        if isinstance(e, _GeminiRefused):
            if not e.searched:
                _release_search(reserved_on, model, e.daily_quota)
            raise e.as_error() from None
        raise DependencyUnavailableError(_UNAVAILABLE) from None
    queries = max(1, _search_query_count(data))
    if queries > 1:  # the 3.x models bill every query the model ran
        _count_searches(queries - 1)
    paid_queries = max(0, queries - free_before)
    usage = translate_engines.gemini_usage(data.get("usageMetadata"))
    cost = 0.0
    if not free_tier:
        cost = translate_engines.estimate_cost(model, usage["input_tokens"],
                                               usage["output_tokens"])
        cost += GROUNDED_PAID_PRICE_USD * paid_queries
    db.log_usage(drama_id, "gemini", model, "metadata_research", usage["input_tokens"],
                 usage["output_tokens"], cost)
    try:
        parsed = _parse_response(data)
    except Exception:
        log.warning("Grounded research returned an unreadable response.")
        raise DependencyUnavailableError(_UNAVAILABLE) from None
    if not parsed["fields"]:
        withheld = _withheld_reason(data)
        if withheld is not None:
            raise withheld
    result = {"mode": mode, "model": model, "retrieved_at": _now().isoformat() + "Z",
              "cost_usd": round(cost, 6), **parsed}
    if parsed["fields"]:  # an empty answer is not cached, so it can be retried
        db.put_research_cache(key, result)
    return _response(drama_id, drama, key, result, cached=False)


def _reserve_search(mode: str, model: str, allow_paid: bool, free_tier: bool) -> tuple:
    """Decides free vs paid, checks the paid opt-in and the monthly cap, and
    takes one search and one of the model's requests, all under one lock, so
    two lookups at once can't both use the last free searches and a refused
    lookup counts nothing. A failed call still counts (Google may have run
    the search). Returns (paid, free searches left before this lookup, the
    counter day it was taken on)."""
    with _budget_lock:
        state = _usage()
        free_before = _free_remaining(state, model, free_tier)
        paid = not _runs_free(state, model, free_tier)
        if paid:
            _refuse_paid(allow_paid, free_tier, state, model)
        _check_cap(mode, model, paid)
        _add(state, model, 1, 1)
        _save(state)
        return paid, free_before, state["date"]


def _release_search(reserved_on: str, model: Optional[str] = None,
                    daily_quota: Optional[dict] = None):
    """Gives back what _reserve_search took on `reserved_on`, for a request
    Google refused before running any search. A counter that has since
    rolled over to a new day or month keeps its count. When the refusal was
    a used-up daily quota, the model (or, for the search allowance, every
    model) counts as used up for the rest of today, and a free-tier limit
    Google named replaces the model's usual one."""
    with _budget_lock:
        state = _usage()
        today = _today()
        if today == reserved_on:
            state["count"] = max(0, state["count"] - 1)
            if model:
                state["models"][model] = max(0, state["models"].get(model, 0) - 1)
        if today[:7] == reserved_on[:7]:
            state["month_count"] = max(0, state["month_count"] - 1)
        if daily_quota is not None and today == reserved_on:
            hit = list(MODELS) if daily_quota["searches"] else [daily_quota["model"] or model]
            state["used_up"] = [m for m in MODELS if m in state["used_up"] or m in hit]
        if daily_quota and daily_quota["limit"] and not daily_quota["searches"] \
                and today == reserved_on \
                and (daily_quota["model"] or model) in MODELS:
            state["learned_rpd"][daily_quota["model"] or model] = \
                min(daily_quota["limit"], _MAX_LEARNED_RPD)
        _save(state)


def apply_research(drama_id: int, research_id: Optional[str], choices: dict,
                   seen: Optional[dict] = None, principal=None, *, mode: Optional[str] = None,
                   model: Optional[str] = None, retrieved_at: Optional[str] = None) -> dict:
    """choices: {field: "keep" | "replace" | "save_both" | "confirm"}. Values
    come from the cached research result, never from the client. `seen`
    holds, per chosen field, the drama's value the user was shown next to the
    research; if it has changed since (an edit in Details, another user),
    nothing is written (409), so a value the user never saw is never
    replaced. "confirm" records the sources for a value that already matches.
    Returns
    {"drama_id", "replaced", "saved_alternates", "kept", "drama"}."""
    drama = require_drama(drama_id)
    if not isinstance(choices, dict) or not choices:
        raise InvalidInputError("Choose Keep, Replace or Save both for at least one field.")
    if research_id is None:
        research_id = _snapshot_cached(drama_id, drama, mode, model, retrieved_at)
    elif not isinstance(research_id, str) or not _RESEARCH_ID.match(research_id):
        raise InvalidInputError("research_id is not valid.")
    result = db.get_research_result(research_id, drama_id)  # this drama's lookup only
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
            raise ConflictError("This title's details changed since the research was shown. "
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
    require_drama(drama_id)
    return {"drama_id": drama_id, "fields": db.list_field_provenance(drama_id)}
