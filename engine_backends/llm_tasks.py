"""Single-prompt LLM calls for features other than a translation batch:
speaker tagging, pacing, consistency, summaries and flagging."""

import re
from .gemini import GeminiEngine
from .local import OllamaEngine, _ollama_chat, estimate_ollama_num_ctx, strip_ollama_thinking
from .openai_compat import OpenAIEngine
from .shared import (
    _id_keyed_batch_request,
    build_numbered_lines,
    call_with_backoff,
    extract_first_json_value,
    parse_json_array,
    read_json_capped,
    redact_secrets,
    request_translations_with_retry,
)


def call_llm_json(engine, prompt: str, max_tokens: int = 2000, fallback: str = "[]",
                   usage_cb=None) -> str:
    """
    Shared single-prompt LLM call for every feature that isn't a
    translation batch: emotion tagging, translation notes, glossary
    extraction, adaptive style analysis, story tools, the universe wiki,
    line tools, Q&A, dictionary's LLM fallback, Scanlate's page
    translation, metadata lookup, and bulk import.

    Until this existed, each of those ~11 call sites carried its own
    copy of this function, and every copy only handled two of this
    app's three LLM call shapes: Claude's Messages API and an
    OpenAI-compatible chat client (DeepSeek/etc.). GeminiEngine has
    neither -- it calls Gemini's REST endpoint directly with `requests`
    -- so picking Gemini as the engine made every one of these features
    silently return nothing (an empty result, not an error) instead of
    a real answer. Confirmed directly: `hasattr(engine, "client")` is
    False for GeminiEngine, so every copy fell through to its "decline
    quietly" branch.

    usage_cb, if given, is called with (input_tokens, output_tokens)
    after a successful call, the same shape already used by the main
    Translate job's own usage_cb -- so a caller can log real spend here
    too, instead of only translation ever reaching the cost dashboard.
    """
    client = getattr(engine, "client", None)
    if client is not None and hasattr(client, "messages"):
        resp = call_with_backoff(lambda: client.messages.create(
            model=engine.model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ))
        if usage_cb and hasattr(resp, "usage"):
            usage_cb(getattr(resp.usage, "input_tokens", 0),
                     getattr(resp.usage, "output_tokens", 0))
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    if client is not None:
        resp = call_with_backoff(lambda: client.chat.completions.create(
            model=engine.model, messages=[{"role": "user", "content": prompt}],
        ))
        if usage_cb and getattr(resp, "usage", None):
            usage_cb(getattr(resp.usage, "prompt_tokens", 0),
                     getattr(resp.usage, "completion_tokens", 0))
        return resp.choices[0].message.content.strip()

    if isinstance(engine, GeminiEngine):
        import requests
        engine._throttle_for_free_tier()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{engine.model}:generateContent")
        resp = call_with_backoff(lambda: requests.post(
            url, headers={"x-goog-api-key": engine.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=120, stream=True))
        data = read_json_capped(resp, 120)
        usage = data.get("usageMetadata") or {}
        if usage_cb:
            usage_cb(usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0))
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError):
            return fallback

    if isinstance(engine, OpenAIEngine):
        return call_with_backoff(lambda: engine.chat(
            [{"role": "user", "content": prompt}], usage_cb=usage_cb))

    if isinstance(engine, OllamaEngine):
        data = _ollama_chat(engine.base_url, {
            "model": engine.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": estimate_ollama_num_ctx(prompt, "")},
        })
        if usage_cb:
            usage_cb(data.get("prompt_eval_count", 0), data.get("eval_count", 0))
        return strip_ollama_thinking(data["message"]["content"])

    if getattr(engine, "client", True) is None:
        # An engine with an empty LLM client slot declines cleanly: fallback
        # is already a valid, correctly-shaped "nothing found" result for
        # every caller (an empty list/object or string).
        return fallback

    raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't run this feature.")


# Part of narration tagging's checkpoint key -- bump it when the
# prompt below changes, so labels from the old prompt aren't reused.
TAG_SPEAKERS_PROMPT_VERSION = "1"


def tag_speakers_by_id(id_to_zh: dict, engine, known_characters=None, batch_size: int = 15,
                       usage_cb=None, done=None, on_batch=None, cancel_check=None):
    """For novel narration mode (no audio, no diarization available):
    asks the translation engine to guess who's speaking each chunk --
    a character name, or 'Narrator' for descriptive prose. Works with
    any LLM-capable engine (Claude, DeepSeek); pure-MT engines (NLLB)
    can't do this and will return 'Narrator' for everything.

    id_to_zh maps each chunk's own id (its line idx) to its text. Returns
    {id: label} with an entry for EVERY id given: a label the model
    didn't return for an id (after one retry) is "Narrator", and ids the
    model invented are ignored -- a label can only ever land on the chunk
    it was keyed to, never by list position.
    This is a best-effort heuristic -- always let the user correct
    labels in the review table afterwards.

    Resume: `done` ({id: label}) holds labels an earlier,
    interrupted run already paid for -- a batch whose ids are all in it is
    not sent again. `on_batch({id: label})` is called after each new batch
    (the caller checkpoints it). `cancel_check` is called before each batch
    that would be sent; it may raise to stop the run."""
    if not getattr(engine, "supports_reference", False):
        return {i: "Narrator" for i in id_to_zh}

    known = ", ".join(known_characters) if known_characters else "(none known yet)"
    labels = {}
    done = done or {}
    all_ids = list(id_to_zh)
    for start in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[start:start + batch_size]
        if all(i in done for i in batch_ids):
            labels.update({i: done[i] for i in batch_ids})
            continue
        if cancel_check:
            cancel_check()

        def call_model(numbered, known=known):
            prompt = (
                "For each numbered chunk of Chinese novel text below, identify who is "
                "speaking. If it's dialogue attributed to a specific character, return "
                "that character's name (romanized consistently). If it's narration/"
                "description with no speaking character, return 'Narrator'. "
                f"Known characters so far: {known}. Prefer reusing a known name over "
                "inventing a new one when it's clearly the same person.\n\n"
                'Return ONLY a JSON object mapping each number to its speaker label, e.g. '
                '{"1": "Xiaoling", "2": "Narrator"}. Include EVERY number you were given, '
                "and no numbers you weren't. No preamble, no markdown fences.\n\n" + numbered
            )
            # Reuse whichever engine's underlying client is available for a raw completion.
            return call_llm_json(engine, prompt, max_tokens=2000, fallback="{}", usage_cb=usage_cb)

        # Id-keyed, same reasoning/mechanism as translation's own fix: a
        # plain positional array silently misassigns a chunk's label to
        # the wrong chunk if the response comes back short, long, or
        # reordered. Missing ids retry once, then default to "Narrator"
        # (the safe fallback for novel narration) rather than staying
        # blank.
        result_map = _id_keyed_batch_request(
            batch_ids,
            lambda ids: build_numbered_lines(ids, [id_to_zh[i] for i in ids]),
            call_model)
        for i in batch_ids:
            labels[i] = (result_map.get(str(i)) or "").strip() or "Narrator"
        if on_batch is not None:
            on_batch({i: labels[i] for i in batch_ids})
    return labels


def smart_segment_lines(en_lines, target_wpm: float = 160, min_seconds: float = 1.2):
    """Post-translation pacing pass (idea borrowed from KrillinAI/
    VideoLingo): flags English lines that are too short to read
    comfortably at their assigned duration, or too long to say
    naturally within it, using a simple words-per-minute estimate.
    Returns a list of {idx, issue, suggestion} for lines worth a second
    look before dubbing -- this doesn't auto-edit anything, just tells
    you where the pacing is likely to feel rushed or dragged out."""
    flags = []
    for ln in en_lines:
        duration = max(ln.end - ln.start, 0.01)
        word_count = len(ln.en.split())
        needed_seconds = word_count / (target_wpm / 60)
        if needed_seconds > duration * 1.15:
            flags.append({"idx": ln.idx, "issue": "too_long_for_slot",
                          "detail": f"~{needed_seconds:.1f}s needed, only {duration:.1f}s available"})
        elif duration > min_seconds and needed_seconds < duration * 0.4:
            flags.append({"idx": ln.idx, "issue": "very_short_relative_to_slot",
                          "detail": f"~{needed_seconds:.1f}s needed, {duration:.1f}s available -- "
                                    "may sound like a long pause"})
    return flags


def rewrite_for_pacing_llm(lines_to_fix, engine, batch_size: int = 15, usage_cb=None):
    """For lines flagged as too long to say in their time slot: asks
    the LLM to rewrite them more concisely while preserving meaning,
    so the dub actually fits. Only touches .en; leaves .zh untouched.
    lines_to_fix: list of Line objects (already has .en set)."""
    if not getattr(engine, "supports_reference", False) or not lines_to_fix:
        return lines_to_fix
    for start in range(0, len(lines_to_fix), batch_size):
        batch = lines_to_fix[start:start + batch_size]

        def call_model(numbered):
            prompt = (
                "These English subtitle lines need to be shortened so they can be spoken "
                "naturally within their time slot. Rewrite each one more concisely -- cut "
                "filler words, tighten phrasing -- while keeping the same meaning and tone. "
                'Return ONLY a JSON object mapping each number to its rewritten line, e.g. '
                '{"1": "Shortened line.", "2": "Another one."}. Include EVERY number you '
                "were given, and no numbers you weren't. No preamble, no markdown fences.\n\n"
                + numbered
            )
            return call_llm_json(engine, prompt, max_tokens=2000, fallback="{}", usage_cb=usage_cb)

        # Id-keyed for the same reason as translation itself: a plain
        # positional array silently misassigns a rewrite to the wrong
        # line if the response comes back short, long, or reordered.
        # Missing ids retry once, then fall back to leaving that line's
        # existing .en untouched rather than blanking it.
        rewritten = request_translations_with_retry([ln.en for ln in batch], None, call_model)
        for ln, new_text in zip(batch, rewritten):
            if new_text.strip():
                ln.en = new_text.strip()
    return lines_to_fix


def build_consistency_prompt(batch: list) -> str:
    """The consistency-check prompt for one window of already-translated
    lines. Position-based (1., 2., ...), not id-keyed -- an issue names a
    TERM ("a character's name spelled two ways"), never a specific line,
    so there's no per-line id for the model to echo back here. Shared by
    check_consistency_llm (live) and bulk_translate.py's bulk submission:
    both send byte-for-byte the same prompt for the same
    window of lines."""
    pairs = "\n".join(f"{i+1}. {ln.zh} -> {ln.en}" for i, ln in enumerate(batch))
    return (
        "Below are Chinese source lines paired with their English translations, from the "
        "same drama. Look for CONSISTENCY issues: the same Chinese name, term, or recurring "
        "phrase translated differently in different lines (e.g. a character's name spelled "
        "two ways, or a recurring term like a nickname/title rendered inconsistently). "
        "Ignore normal translation variation for ordinary sentences -- only flag terms that "
        "should clearly stay fixed (names, titles, recurring phrases) but don't.\n\n"
        'Return ONLY a JSON array of objects: [{"term": "...", "variants": ["...", "..."], '
        '"note": "brief description"}]. Empty array if nothing found. No preamble, no '
        "markdown fences.\n\n" + pairs
    )


def check_consistency_llm(lines, engine, batch_size: int = 60, usage_cb=None, cancel_check=None):
    """Reviews already-translated lines for consistency issues: the same
    Chinese term/name translated differently in different places. Works
    on the .zh/.en pairs already present -- doesn't call any external
    dictionary, just asks the LLM to spot drift across the batch it's
    given. Returns (issues, failed_batches, total_batches):
    issues is a list of {"term", "variants": [...], "note"} for review --
    doesn't auto-fix anything, since the "right" choice depends on
    context you'd want to confirm yourself. failed_batches/total_batches
    let the caller tell "nothing to flag" apart from "some
    batches silently couldn't be checked at all" -- previously a batch
    that errored or came back empty was skipped with no trace, so a run
    that failed on every batch looked identical to one that genuinely
    found nothing.

    Only meaningful with an LLM-capable engine; pure-MT engines return
    ([], 0, 0) (they don't reason about the whole set at once).
    cancel_check: called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes)."""
    if not getattr(engine, "supports_reference", False):
        return [], 0, 0
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return [], 0, 0

    issues = []
    failed_batches = 0
    total_batches = 0
    for start in range(0, len(translated), batch_size):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        total_batches += 1
        prompt = build_consistency_prompt(batch)
        try:
            text = call_llm_json(engine, prompt, max_tokens=2000, fallback=None,
                                  usage_cb=usage_cb)
            if text is None:
                failed_batches += 1
                continue
        except Exception as e:
            failed_batches += 1  # a check failing shouldn't block anything -- just skip that batch
            import applog
            applog.get_logger().warning(
                f"consistency check batch {total_batches} failed: {redact_secrets(str(e))}")
            continue
        batch_issues = parse_json_array(text, 0)
        if isinstance(batch_issues, list):
            issues.extend(i for i in batch_issues if isinstance(i, dict) and i.get("term"))
    return issues, failed_batches, total_batches


def build_episode_summary_prompt(lines) -> str:
    """The per-episode running-summary prompt -- one call over the
    WHOLE finished episode's English text, not a per-batch window (unlike
    build_consistency_prompt above), since the point is a fixed, once-per-
    episode artifact that the next episode's translation can afford to
    read in full."""
    body = "\n".join(ln.en.strip() for ln in lines if (ln.en or "").strip())
    return (
        "Below is the complete English translation of one finished episode from an "
        "ongoing series. Write a short running summary for continuity into the NEXT "
        "episode of the same series: key events, any unresolved plot threads or "
        "questions, and each named character's state at the end of this episode "
        "(relationships, secrets revealed or still hidden, where they ended up). A "
        "few sentences of plain prose -- no preamble, no episode-by-episode recap of "
        "earlier episodes, just what a translator picking up the next episode cold "
        "would need to resolve a callback or an ambiguous reference correctly.\n\n"
        'Return ONLY a JSON object: {"summary": "..."}. No markdown fences, no other '
        "keys.\n\n" + body
    )


def generate_episode_summary(lines, engine, usage_cb=None) -> str:
    """One LLM call per finished episode (never once per
    translation batch) producing a short running summary for cross-
    episode narrative continuity -- distinct from glossary/translation
    memory's terminology-only continuity. Stored on the drama row
    (dramas.episode_summary) by the caller; this function only generates
    the text.

    Declines quietly (returns "") for a pure-MT engine that can't reason
    about a whole episode's text (same supports_reference guard as
    check_consistency_llm/flag_uncertain_lines above), when there's
    nothing translated yet to summarize, or when the call itself fails --
    a missing/unreachable summary engine must never block or fail the
    translation run that just finished."""
    if not getattr(engine, "supports_reference", False):
        return ""
    translated = [ln for ln in lines if (ln.en or "").strip()]
    if not translated:
        return ""
    prompt = build_episode_summary_prompt(translated)
    try:
        text = call_llm_json(engine, prompt, max_tokens=500, fallback=None, usage_cb=usage_cb)
        if text is None:
            return ""
    except Exception as e:
        import applog
        applog.get_logger().warning(f"episode summary generation failed: {redact_secrets(str(e))}")
        return ""
    data = extract_first_json_value(text)
    if isinstance(data, dict) and isinstance(data.get("summary"), str):
        return data["summary"].strip()
    return ""


# Kept intentionally to what's actually assessable from the text alone --
# "speaker uncertain" and "overlapping speech"/"audio unclear" would need
# real diarization-confidence or audio evidence this codebase doesn't
# expose yet (see diarize.py), not something worth guessing at from text.
FLAG_REASONS = {
    "uncertain_translation": "Possible mistranslation or awkward phrasing worth a second look",
    "ambiguous_reference": "A pronoun or reference ('she', 'that place') that isn't clearly resolved",
    "name_uncertain": "A name or term that might be misspelled or inconsistently romanized",
    "slang_idiom": "Slang or an idiom that may not have translated well",
}

# Flags the app sets itself (not offered to the LLM as a reason to pick).
SYSTEM_FLAG_REASONS = {
    "timing_uncertain": "Timing uncertain -- forced alignment fell back to approximate timing",
    "language_uncertain": ("Language uncertain -- the text doesn't match the language detected "
                           "for this line"),
    "timing_overlap": "Overlaps the next line -- exports trim it",
    "timing_drift": "Timing disagrees with the audio -- the line may start or end away from the speech",
    "reading_speed": "Too fast to read -- too many characters for the time it's shown",
    "factual_detail": ("Auto QC: a number, date, name, amount or unit differs between the "
                       "source and the translation"),
    "bulk_source_changed": ("Source text changed while a bulk translation was pending -- its "
                            "result wasn't applied; translate this line again"),
    "pronoun_check": ("Pronoun check -- the translation says he/him but no he/him character "
                      "is set for this speaker"),
    "content_blocked": ("Blocked by the translation engine's own content-moderation system -- "
                        "see the note for which engine and its stated reason"),
}


def flag_reason_label(flag: str) -> str:
    return FLAG_REASONS.get(flag) or SYSTEM_FLAG_REASONS.get(flag) or flag


def matching_glossary_terms(zh: str, glossary_terms) -> list:
    """Glossary entries whose source term (or a recorded alias)
    literally appears in zh -- the same "in play for this line" heuristic
    line_tools.explain_translation already used, factored out so the
    "what happened here?" view can show the same real, non-fabricated
    match set instead of re-deriving it differently."""
    def _term_forms(t):
        aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
        return [t.get("term_original", "")] + aliases
    return [t for t in (glossary_terms or []) if any(f and f in zh for f in _term_forms(t))]


def build_flag_prompt(batch: list, id_fn=lambda ln: ln.idx) -> str:
    """The review-queue prompt for one batch of already-translated lines,
    each numbered by id_fn(ln) (its position by default, matching what
    flag_uncertain_lines' own by_idx lookup expects back). bulk_translate.py's
    bulk submission passes id_fn=lambda ln: ln.id instead --
    results can come back hours later, by which point a position-based id
    could point at an entirely different line if the drama was edited in
    the meantime, where the permanent line id can't."""
    reasons_desc = "\n".join(f"  - {k}: {v}" for k, v in FLAG_REASONS.items())
    pairs = "\n".join(f"[{id_fn(ln)}] {ln.zh} -> {ln.en}" for ln in batch)
    return (
        "Below are Chinese source lines paired with their English translations. Flag ONLY "
        "the lines that genuinely need a second look -- most lines need none at all, and "
        "over-flagging defeats the point (the person reviewing this can't tell a real issue "
        "from noise). Reasons worth flagging:\n\n"
        f"{reasons_desc}\n\n"
        'Return ONLY a JSON array: [{"line_idx": 0, "reason": "uncertain_translation", '
        '"note": "brief reason"}]. Empty array if nothing needs flagging (the common case). '
        "No preamble, no markdown fences.\n\n" + pairs
    )


def flag_uncertain_lines(lines, engine, batch_size: int = 30, progress_cb=None, usage_cb=None,
                         cancel_check=None):
    """
    Reviews already-translated lines and flags the ones worth a second
    look -- the review-queue idea: instead of scanning a whole multi-hour
    transcript line by line, review just the handful the model itself
    wasn't confident about. Doesn't touch the translation itself, just
    annotates .flag/.flag_note on the Line objects it's given (mutated in
    place, same convention as translate_lines_with_engine).

    Only meaningful with an LLM-capable engine; pure-MT engines (NLLB)
    can't reason about their own confidence and are left
    untouched -- every line's .flag stays whatever it already was.
    cancel_check: called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes).
    """
    if not getattr(engine, "supports_reference", False):
        return lines
    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return lines

    n_batches = (len(translated) + batch_size - 1) // batch_size
    failed_batches = 0

    for bi, start in enumerate(range(0, len(translated), batch_size)):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        prompt = build_flag_prompt(batch)
        try:
            text = call_llm_json(engine, prompt, max_tokens=2000, fallback="[]",
                                  usage_cb=usage_cb)
        except Exception:
            text = "[]"  # a check failing shouldn't block anything -- just skip that batch
            failed_batches += 1

        if progress_cb:
            progress_cb((bi + 1) / n_batches)

        flagged = parse_json_array(text, 0)
        if not isinstance(flagged, list):
            continue
        by_idx = {ln.idx: ln for ln in batch}
        for f in flagged:
            if not isinstance(f, dict) or f.get("line_idx") is None:
                continue
            ln = by_idx.get(int(f["line_idx"]))
            if ln is None:
                continue
            reason = f.get("reason") if f.get("reason") in FLAG_REASONS else "uncertain_translation"
            ln.flag = reason
            ln.flag_note = f.get("note", "")
    if failed_batches:
        # Those batches were not checked, which is not the same as "no issues".
        import applog
        applog.get_logger().warning(
            "flag check failed for %d of %d batches; their lines were not checked",
            failed_batches, n_batches)
    return lines
