"""
services/glossary_spelling_service.py -- two helpers for a name the ASR spells several
ways: "Suggest spellings" (one bounded LLM call that proposes how the title's language
may have written a known name, verified against the transcript) and a read-only check
of lines whose English renders the name differently.

The model only guesses. A proposal is shown when it really occurs in the title's source
lines, with its count and example lines; anything else is dropped, so a wrong guess never
reaches the owner. Nothing is saved here: the owner confirms each one as an alias through
the normal term update.

The call is a background job (cancellable, deadline and retry notices from
glossary_extract_service._bounded_calls, usage logged, refused when the monthly cap is
spent). The consistency check is offline and free. Title visibility is checked by the
route's `{drama_id}` path guard (api/auth.py). No FastAPI import.
"""
import json
import re
import unicodedata
from collections import Counter
from typing import List, Optional

import background_jobs
import db
import translate_engines
from engine_backends import llm_tasks, paid_calls
from engine_backends.shared import TranslationCancelled, language_name
from services import glossary_extract_service as extract
from services import glossary_service
from services.glossary_common import MAX_TERM_LEN, _drama, _text
from services.glossary_variants_service import (_LATIN_STOPWORDS, SCAN_MAX_CHARS, SCAN_MAX_LINES, _norm,
                                                _snippet, _source_lines)
from lib.errors import ConflictError, InvalidInputError, NotFoundError

# Functions that call an LLM engine (and so may spend on a paid one).
PAID_ENGINE_FUNCTIONS = ("start_spelling_suggestions",)

MAX_ENGLISH_LEN = 80
MAX_MODEL_SPELLINGS = 20      # read from the reply; a small model may ramble past what was asked
MAX_SHOWN = 10                # more than a person reads; the most frequent first
MAX_EXAMPLES = 3
MIN_SPELLING_LEN = 2          # one character matches half the transcript
MAX_KNOWN_FORMS = 6           # the term plus a few aliases tell the model what to look for
REPLY_MAX_TOKENS = 600
UNUSABLE_MESSAGE = "The model's answer could not be used. Nothing was changed; try again or pick another engine."
NO_MATCH_MESSAGE = "None of the model's guesses appear in this title's lines."

MAX_CONSISTENCY_ROWS = 50
MAX_OTHER_WORDS = 8

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_LATIN_WORD = re.compile(r"\b[A-Z][a-z]{2,15}\b")


def spelling_job_id(drama_id: int) -> str:
    return f"spelling_suggest_{drama_id}"


def spelling_engine(drama_id: int) -> str:
    """The engine a run would use, for the router's engines.paid gate."""
    return extract.novel_glossary_engine(drama_id)


def _clean_english(english) -> str:
    return _text(english, "english", MAX_ENGLISH_LEN, required=True)


def _find_term(drama_id: int, term_id) -> dict:
    if isinstance(term_id, bool) or not isinstance(term_id, int):
        raise InvalidInputError("term_id must name a glossary term.")
    for t in glossary_service.list_glossary_terms(drama_id):
        if t["id"] == term_id:
            return t
    raise NotFoundError("Glossary term not found.")


def build_prompt(language: str, english: str, known_forms: List[str]) -> str:
    lang = language_name(language)
    known = ", ".join(known_forms)
    return (
        f"A speech-recognition system transcribed {lang} audio and may have written one "
        f"person's or thing's name several different ways. The name in English is "
        f"\"{english}\". The spellings already known in {lang} are: {known}.\n\n"
        f"List up to {MAX_MODEL_SPELLINGS} other ways this name could plausibly have been "
        f"transcribed in {lang} (similar-sounding characters or letters, common mishearings). "
        "Do not translate it and do not explain.\n"
        'Return ONLY a JSON array of strings, for example ["...", "..."]. '
        "No preamble, no markdown fences.")


def parse_spellings(reply) -> Optional[List[str]]:
    """The strings in a JSON array reply (or an object holding one), or None when the
    reply is not that. Strict on purpose: a small model's prose must not be split into
    'spellings'. Duplicates are dropped; an empty list is a valid answer."""
    if not isinstance(reply, str) or not reply.strip():
        return None
    try:
        data = json.loads(_FENCE.sub("", reply.strip()).strip())
    except ValueError:
        return None
    if isinstance(data, dict):
        data = next((data[k] for k in ("spellings", "proposals", "variants") if isinstance(data.get(k), list)),
                    None)
    if not isinstance(data, list):
        return None
    out: List[str] = []
    for item in data:
        if isinstance(item, dict):
            item = item.get("spelling") or item.get("text")
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out[:MAX_MODEL_SPELLINGS]


def _pattern(key: str) -> re.Pattern:
    """`key` is already NFKC-normalised: it is searched in NFKC-normalised lines."""
    escaped = re.escape(key)
    # A Latin spelling must not count inside a longer word (Yalo inside Yaloch).
    if re.fullmatch(r"[A-Za-z' -]+", key):
        escaped = rf"(?<![A-Za-z]){escaped}(?![A-Za-z])"
    return re.compile(escaped, re.IGNORECASE)


def verify_spellings(spellings: List[str], lines, term_text: str, aliases: List[str],
                     other_terms: set) -> List[dict]:
    """The spellings that occur in `lines` ([(idx, text)]), most frequent first, each with
    its count and example lines. The term itself is never offered.

    Every check runs on the NFKC-normalised guess, the string that is actually searched: a
    half-width ｶﾞ is two code points but one character after normalising, and would
    match nearly every line. The text offered is the form the transcript uses (the most
    common one), not the model's: alias matching in translation is a literal, case-sensitive
    substring, so "yalo" saved as an alias would not match "YALO"."""
    alias_set = {_norm(a) for a in aliases}
    term_key = _norm(term_text)
    normalized = [(idx, unicodedata.normalize("NFKC", text)) for idx, text in lines]
    found, seen = [], set()
    for spelling in spellings:
        key = unicodedata.normalize("NFKC", spelling).strip()
        folded = key.casefold()
        if len(key) < MIN_SPELLING_LEN or len(key) > MAX_TERM_LEN or folded == term_key or folded in seen:
            continue
        seen.add(folded)
        pattern = _pattern(key)
        forms: Counter = Counter()
        examples = []
        for idx, text in normalized:
            for m in pattern.finditer(text):
                forms[m.group()] += 1
                if len(examples) < MAX_EXAMPLES:
                    examples.append({"line_idx": idx, "snippet": _snippet(text, m.start(), m.end())})
        if forms:
            shown = min(forms, key=lambda f: (-forms[f], f))
            found.append({"text": shown, "count": sum(forms.values()),
                          "is_alias": _norm(shown) in alias_set,
                          "is_term": _norm(shown) in other_terms, "examples": examples})
    found.sort(key=lambda c: (-c["count"], c["text"]))
    return found[:MAX_SHOWN]


def _run_job(job_id, run_id, drama_id, engine, engine_name, term_text, aliases, english,
             language, other_terms):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    background_jobs.update_progress(job_id, 0.1, "Asking the model for likely spellings...")
    prompt = build_prompt(language, english, [term_text, *aliases][:MAX_KNOWN_FORMS])
    try:
        with extract._bounded_calls(job_id, drama_id, engine, engine_name, "glossary_spellings"):
            reply = llm_tasks.call_llm_json(
                engine, prompt, max_tokens=REPLY_MAX_TOKENS, fallback="",
                usage_cb=paid_calls.usage_logger(drama_id, engine_name, engine,
                                                 "glossary_spellings"))
    except (TranslationCancelled, background_jobs.JobCancelled):
        raise background_jobs.JobCancelled() from None
    except Exception as exc:
        raise RuntimeError("Suggesting spellings failed. "
                           + translate_engines.redact_secrets(str(exc))) from None
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()
    spellings = parse_spellings(reply)
    result = {"run_id": run_id, "term": term_text, "english": english, "proposals": [],
              "unusable": spellings is None, "lines_scanned": 0, "truncated": False}
    if spellings is not None:
        lines, truncated = _source_lines(drama_id)
        result.update(proposals=verify_spellings(spellings, lines, term_text, aliases, other_terms),
                      lines_scanned=len(lines), truncated=truncated)
    background_jobs.set_result(job_id, result)


def start_spelling_suggestions(drama_id: int, term_id: int, english: str,
                               engine_name: Optional[str] = None) -> dict:
    """Starts one bounded model call proposing how `english` (a glossary term's known
    name) may be spelled in the title's language. Paid-engine spend: yes, unless the
    engine is in FREE_ENGINES; refused first when this month's cap is spent.

    engine_name is the engine the route's gate checked (ConflictError if the title's
    engine changed since). Poll get_spelling_status. NotFoundError, InvalidInputError,
    UnsupportedOperationError (engine can't, cap spent), MissingKeyError, ConflictError
    (already running)."""
    from services import settings_service, translate_run_service

    drama = _drama(drama_id)
    english = _clean_english(english)
    term = _find_term(drama_id, term_id)
    engine_name, engine = extract._glossary_engine(drama, engine_name)
    translate_run_service.refuse_when_cap_spent(engine_name, settings_service.get("gemini_free_tier"))
    other_terms = {_norm(t["term_original"]) for t in glossary_service.list_glossary_terms(drama_id)
                   if t["id"] != term["id"]}
    job_id = spelling_job_id(drama_id)
    started = extract._start_extraction_job(
        job_id, _run_job, drama_id, engine, engine_name, term["term_original"],
        list(term["aliases"]), english, drama.get("source_language") or "zh", other_terms,
        gpu_touching=engine_name == "ollama",
        description=f"Suggest spellings (title #{drama_id})")
    if not started:
        raise ConflictError("Spelling suggestions are already running for this title.")
    return {"job_id": job_id, "engine": engine_name}


def get_spelling_status(drama_id: int) -> dict:
    """{job_id, status, progress, message, run_id, result}; result only when done.
    A first look, before any run in this process, is status "idle"."""
    _drama(drama_id)
    job_id = spelling_job_id(drama_id)
    job = background_jobs.get_status(job_id)
    if not job:
        return {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "run_id": None,
                "result": None}
    status = job.get("status")
    message = job.get("error") if status == "error" else job.get("message")
    result = None
    if status == "done":
        r = job.get("result") or {}
        result = {k: r.get(k) for k in ("term", "english", "proposals", "unusable", "lines_scanned", "truncated")}
        if r.get("unusable"):
            message = UNUSABLE_MESSAGE
        elif not r.get("proposals"):
            message = NO_MATCH_MESSAGE
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": translate_engines.redact_secrets(str(message)) if message else "",
            "run_id": extract._current_run_id(job_id, job), "result": result}


def cancel_spelling_run(drama_id: int, run_id: str, principal) -> dict:
    return extract._cancel_extraction(drama_id, spelling_job_id(drama_id), run_id, principal)


# --- Read-only consistency check ---------------------------------------------

def _english_pattern(english: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(unicodedata.normalize('NFKC', english).strip())}(?!\w)", re.IGNORECASE)


def check_name_consistency(drama_id: int, term_id: int, english: Optional[str] = None) -> dict:
    """Lines whose source mentions the term (or an alias) but whose English does not
    contain `english` (default: the term's saved translation). Report only: nothing is
    changed. `other_words` are capitalised words in those lines, a hint at what the
    name was rendered as instead. Lines with no English yet are counted, not listed."""
    _drama(drama_id)
    term = _find_term(drama_id, term_id)
    expected = (_clean_english(english) if english is not None and str(english).strip()
                else (term.get("term_translation") or "").strip())
    base = {"term": term["term_original"], "english": expected}
    if not expected:
        return {**base, "available": False,
                "reason": "Type the English name first (this term has no translation saved).",
                "lines_with_name": 0, "untranslated": 0, "consistent": 0, "differing": [],
                "other_words": [], "lines_scanned": 0, "truncated": False, "more_rows": False}
    forms = [_norm(f) for f in [term["term_original"], *term["aliases"]] if f]
    pattern = _english_pattern(expected)
    mentions = untranslated = consistent = scanned = chars = 0
    truncated = False
    rows, others = [], Counter()
    for ln in db.load_line_objects(drama_id):
        if ln.sfx or not (ln.zh or "").strip():
            continue
        if scanned >= SCAN_MAX_LINES or chars + len(ln.zh) > SCAN_MAX_CHARS:
            truncated = True
            break
        scanned += 1
        chars += len(ln.zh)
        source = unicodedata.normalize("NFKC", ln.zh)
        folded = _norm(source)
        if not any(f in folded for f in forms):
            continue
        mentions += 1
        en = (ln.en or "").strip()
        if not en:
            untranslated += 1
        elif pattern.search(unicodedata.normalize("NFKC", en)):
            consistent += 1
        else:
            rows.append({"line_idx": ln.idx, "source": source, "english": en})
            for m in _LATIN_WORD.finditer(en):
                if m.start() and m.group().lower() not in _LATIN_STOPWORDS:
                    others[m.group()] += 1
    return {**base, "available": True, "reason": "", "lines_with_name": mentions,
            "untranslated": untranslated, "consistent": consistent,
            "differing": rows[:MAX_CONSISTENCY_ROWS],
            "other_words": [w for w, _ in others.most_common(MAX_OTHER_WORDS)],
            "lines_scanned": scanned, "truncated": truncated,
            "more_rows": len(rows) > MAX_CONSISTENCY_ROWS}
