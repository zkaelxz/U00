"""Reflect mode and the per-run translate loop."""

import inspect
from .fallback import FallbackEngine
from .llm_tasks import call_llm_json
from .pricing import estimate_cost_for_engine
from .prompts import build_batch_context, build_stable_prompt
from .shared import (
    ContentModerationBlocked,
    FreeTierDailyLimitReached,
    _backoff_wait_var,
    _cancel_check_var,
    _id_keyed_batch_request,
    build_numbered_lines,
    call_with_backoff,
    is_english_line,
    spoken_language_tag,
    tagged_line_languages,
    redact_secrets,
)


def build_reflect_faithful_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                  speaker_by_id: dict = None) -> str:
    """Reflect mode's pass 1 (faithfulness) prompt for one batch of ids.
    Shared by reflect_translate_batch (live, in-process) and
    bulk_translate.py's Reflect pipeline -- both build
    byte-for-byte the same prompt for the same ids/lines."""
    numbered = build_numbered_lines(
        ids, [zh_by_id[i] for i in ids],
        [speaker_by_id.get(i) for i in ids] if speaker_by_id else None)
    return (
        f"{instructions}\n\n"
        "This is the FAITHFULNESS pass of a multi-stage translation: translate each line "
        "below preserving its exact literal meaning, grammar and information -- word choice "
        "and register still matter, but don't optimize yet for how naturally it reads as a "
        "subtitle; a later pass polishes that. Keep names and glossary terms consistent as "
        "instructed above.\n\n"
        'Return ONLY a JSON object mapping each line number to its translation, e.g. '
        '{"1": "...", "2": "..."}. No preamble, no markdown fences.\n\n'
        + batch_ctx +
        f"Lines:\n{numbered}"
    )


def build_reflect_reflection_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict) -> str:
    """Reflect mode's pass 2 (reflection/critique) prompt -- see
    build_reflect_faithful_prompt's docstring. draft_by_id: pass 1's own
    saved output for each id (never empty here -- the caller only
    includes ids that got an actual faithfulness draft)."""
    pairs = "\n".join(f"{i}. {zh_by_id[i]} -> {draft_by_id[i]}" for i in ids)
    return (
        f"{instructions}\n\n"
        "This is the REFLECTION pass: for each numbered line below (source -> a literal "
        "draft translation), critique that SPECIFIC draft -- where it's technically correct "
        "but reads unnaturally as a subtitle, plus accuracy, pronoun/gender choices, glossary "
        "use, tone and register. One or two plain sentences per line. Skip a line's key "
        "entirely if the draft is already good -- don't invent a critique to fill space.\n\n"
        'Return ONLY a JSON object mapping each line number to its critique, e.g. '
        '{"1": "..."}. Omit a key entirely for a line that needs no critique. No preamble, '
        "no markdown fences.\n\n"
        + batch_ctx +
        f"Lines:\n{pairs}"
    )


def build_reflect_expressive_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict, critique_by_id: dict) -> str:
    """Reflect mode's pass 3 (expressiveness/rewrite) prompt -- see
    build_reflect_faithful_prompt's docstring. critique_by_id: pass 2's
    own saved output; an id with none (the draft was already judged
    good) just gets no "Critique:" line, same as the live path."""
    parts = []
    for i in ids:
        entry = f"{i}. Source: {zh_by_id[i]}\n   Draft: {draft_by_id[i]}"
        critique = critique_by_id.get(i)
        if critique:
            entry += f"\n   Critique: {critique}"
        parts.append(entry)
    return (
        f"{instructions}\n\n"
        "This is the EXPRESSIVENESS pass: rewrite each line's draft translation using its "
        "critique (where one is given) so it reads naturally as a subtitle -- fluent, "
        "well-paced, in-register -- while keeping the source's exact meaning. A line with no "
        "critique is probably already close; still return your best final wording for every "
        "line.\n\n"
        'Return ONLY a JSON object mapping each line number to its final translation, e.g. '
        '{"1": "..."}. No preamble, no markdown fences.\n\n'
        + batch_ctx +
        "Lines:\n" + "\n".join(parts)
    )


REFLECT_PASSES = ("draft", "critique", "rewrite")


def reflect_translate_batch(engine, zh_lines: list, context: dict, usage_cb=None, max_retries: int = 1,
                            pass_cb=None):
    """
    "High quality" Reflect mode: three separate LLM passes for
    one batch, instead of translate_batch's single call --

      1. Faithfulness: a literal translation preserving exact meaning,
         not yet polished for how it reads.
      2. Reflection: the same engine critiques that SPECIFIC draft --
         where it's technically correct but reads unnaturally, plus
         accuracy, pronouns/gender, glossary use, tone and register.
      3. Expressiveness: rewrites using the reflection, now optimizing
         for how it reads as a subtitle.

    Deliberately three real passes, not VideoLingo's two-call version
    (their reflection is folded invisibly into the rewrite prompt) --
    giving the critique its own pass means it can be stored and shown
    (as translation notes), not just silently baked into a rewrite.

    Goes through call_llm_json, not each engine's own translate_batch:
    call_llm_json already dispatches Claude/DeepSeek/Gemini/Ollama
    uniformly for a single free-form prompt, which is exactly what three
    differently-worded passes need -- translate_batch is fixed to one
    particular (already-natural-reading) prompt shape and isn't reusable
    for this. Every pass is id-keyed via _id_keyed_batch_request, the
    same exact-id matching translate_batch uses -- deliberately
    not VideoLingo's own SequenceMatcher fuzzy-similarity matching, which
    is strictly less robust than an exact id.

    context: same shape translate_batch's engines already take
    (style_note, drama_meta, novel_reference, locale, glossary_terms,
    style_guidelines, recent_context, upcoming_lines, speaker_labels,
    line_ids) -- built once by translate_lines_with_engine either way.

    Returns (translations, critiques): both lists parallel to zh_lines.
    translations is the expressiveness pass's final wording, falling
    back to the faithfulness draft for any line expressiveness never
    returned, then "" if even that never came back. critiques is the
    reflection pass's critique for that line, or "" if it had none (a
    draft judged already good) or the pass never returned one.

    pass_cb: optional callable (pass_no, name) invoked as each pass is
    about to start (pass_no 1-3, name from REFLECT_PASSES), so a caller can
    show which of the three slow calls is running.
    """
    if not getattr(engine, "supports_reference", False):
        raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't run Reflect "
                           "mode (needs an LLM engine, not a translation-only one).")

    ids = list(range(1, len(zh_lines) + 1))
    line_ids = context.get("line_ids")
    if (line_ids is not None and len(line_ids) == len(zh_lines)
            and all(isinstance(i, int) for i in line_ids) and len(set(line_ids)) == len(line_ids)):
        ids = list(line_ids)
    pos = {i: p for p, i in enumerate(ids)}
    speaker_names = context.get("speaker_labels")
    line_languages = context.get("line_languages")
    if line_languages and len(line_languages) == len(zh_lines):
        # Tagged here so all three passes show the model each source's language.
        zh_lines = [spoken_language_tag(lang) + zh for lang, zh in zip(line_languages, zh_lines)]

    # build_llm_instructions() alone never actually inserts the
    # reference novel text anywhere -- only build_stable_prompt()'s own
    # novel_block does that (the normal, non-Reflect path already goes
    # through it). Calling build_llm_instructions() directly here meant
    # Reflect mode's own instructions told the model to consult "the
    # reference novel translation below," but nothing was ever below it.
    instructions, novel_block = build_stable_prompt(context)
    if novel_block:
        instructions = instructions + "\n\n" + novel_block
    batch_ctx = build_batch_context(context.get("recent_context"), context.get("upcoming_lines"))
    batch_ctx = batch_ctx + "\n" if batch_ctx else ""

    def call(prompt):
        return call_llm_json(engine, prompt, max_tokens=4000, fallback="{}", usage_cb=usage_cb)

    def build_faithful_batch(batch_ids):
        return build_reflect_faithful_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids},
            {i: speaker_names[pos[i]] for i in batch_ids} if speaker_names else None)

    def announce(pass_no):
        if pass_cb:
            pass_cb(pass_no, REFLECT_PASSES[pass_no - 1])

    announce(1)
    direct_map = _id_keyed_batch_request(ids, build_faithful_batch, call, max_retries)
    direct = {i: direct_map.get(str(i), "") for i in ids}

    def build_reflection_batch(batch_ids):
        return build_reflect_reflection_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids}, direct)

    # Only ids with an actual faithfulness draft go on to reflection/
    # expressiveness -- there's nothing sensible to critique or rewrite
    # for a line that never got one (an empty "Draft: " in the prompt).
    # No retry on the reflection call itself (unlike the other two
    # passes): a line the model chose NOT to critique is a normal,
    # expected answer here -- most lines in a real batch have nothing
    # worth flagging -- and treating every omitted id as "missing, must
    # retry" would re-request the whole batch on almost every run, since
    # it's rare for every single line to get a critique. An id absent
    # here just means "no critique".
    has_draft_ids = [i for i in ids if direct[i]]
    if has_draft_ids:
        announce(2)
    critique_map = (_id_keyed_batch_request(has_draft_ids, build_reflection_batch, call, max_retries=0)
                   if has_draft_ids else {})

    def build_expressive_batch(batch_ids):
        return build_reflect_expressive_prompt(
            instructions, batch_ctx, batch_ids, {i: zh_lines[pos[i]] for i in batch_ids}, direct,
            {i: critique_map.get(str(i)) for i in batch_ids})

    if has_draft_ids:
        announce(3)
    final_map = (_id_keyed_batch_request(has_draft_ids, build_expressive_batch, call, max_retries)
                if has_draft_ids else {})
    translations = [final_map.get(str(i), direct.get(i, "")) for i in ids]
    critiques = [critique_map.get(str(i), "") for i in ids]
    return translations, critiques


def build_translation_context(engine, drama_meta: dict, style_note: str = "", novel_reference=None,
                              locale: str = "en-US", glossary_terms=None, style_guidelines: str = "",
                              ollama_num_ctx_override: int = None) -> dict:
    """The per-job context every engine's prompt is built from -- shared by
    live translation and bulk submission so both send the same prompt."""
    return {
        "drama_meta": drama_meta,
        "style_note": style_note,
        "novel_reference": novel_reference if getattr(engine, "supports_reference", False) else None,
        "locale": locale,
        "glossary_terms": glossary_terms,
        "style_guidelines": style_guidelines,
        # Never hardcode "zh": a Japanese or Korean drama would be
        # silently mistranslated by an engine that takes a source language.
        "source_language": (drama_meta or {}).get("source_language", "zh"),
        "ollama_num_ctx_override": ollama_num_ctx_override,
    }


# Recorded with each translated line (line_provenance).
# Bump it whenever the translate prompt or its batching changes meaning.
TRANSLATE_PROMPT_VERSION = "1"


# A pause this long between two lines is treated as a scene break. Matches
# core.diagnose_line_coverage's large-gap default so both agree on "large".
SCENE_BREAK_GAP_SECONDS = 3.0


def plan_batches(lines, max_size: int, min_gap: float = SCENE_BREAK_GAP_SECONDS,
                 tail_fraction: float = 0.25) -> list:
    """Splits `lines` into consecutive batches of at most `max_size`, ending a
    batch at a scene break instead of mid-scene where it can.

    A batch may end early only inside its last `tail_fraction` of lines, at
    the largest pause of at least `min_gap` seconds (the later cut wins a tie,
    so batches stay large). With no such pause it is cut at `max_size`, which
    makes a recording with no long silences batch exactly as fixed slices do.
    Pure and order-only, so a resumed run re-plans the lines still left and
    gets the same cuts for the same input.
    """
    batches = []
    start, n = 0, len(lines)
    while start < n:
        end = min(start + max_size, n)
        if end < n:
            earliest = max(start + 1, end - int(max_size * tail_fraction))
            best_cut, best_gap = end, None
            for cut in range(earliest, end + 1):
                gap = lines[cut].start - lines[cut - 1].end
                if gap >= min_gap and (best_gap is None or gap >= best_gap):
                    best_cut, best_gap = cut, gap
            end = best_cut
        batches.append(lines[start:end])
        start = end
    return batches


def _translate_lines_with_engine(lines, engine, drama_meta: dict, batch_size: int = 20,
                                 style_note: str = "", novel_reference=None, progress_cb=None,
                                 save_cb=None, force_retranslate: bool = False,
                                 locale: str = "en-US", glossary_terms=None, usage_cb=None,
                                 style_guidelines: str = "", cancel_check_cb=None,
                                 context_window: int = 6, context_window_ahead: int = 3,
                                 character_names: dict = None, ollama_num_ctx_override: int = None,
                                 reflect: bool = False, notes_cb=None, cost_cap_usd: float = None,
                                 cap_cb=None, target_ids=None, detail_cb=None,
                                 scene_aware_batches: bool = False):
    """scene_aware_batches: start batches at scene breaks (plan_batches)
    instead of cutting fixed slices of batch_size. Same maximum size.

    detail_cb: optional callable (fraction, message) for a job that wants
    finer progress than progress_cb: fires at the start and end of every
    batch, after each Reflect pass starts, and while a retry waits. When
    given it replaces progress_cb entirely, so a caller passes one or the
    other. The fraction never decreases and never exceeds 1.0.

    cancel_check_cb: optional callable returning True if the run should
    stop cooperatively between batches -- e.g. background_jobs.is_cancel_requested,
    so a background translation job can be stopped safely (rather than
    racing a destructive action like a full library reset against a
    thread that's still writing).

    reflect: "High quality" mode -- runs reflect_translate_batch
    (three passes: faithfulness, reflection, expressiveness) per batch
    instead of engine.translate_batch's single pass. notes_cb, if given,
    is called once per batch with a list of {line_idx, term, note_type,
    note} dicts (translation_guide's translation-notes shape) for
    whichever lines the reflection pass actually critiqued -- call
    db.save_translation_notes(...) from it. Ignored when reflect=False.

    ollama_num_ctx_override: optional Settings override for OllamaEngine's
    context-window size. Ignored by every other engine. OllamaEngine
    itself never lets this go below what the actual prompt needs --
    see _estimate_ollama_num_ctx's docstring for why.

    lines: list of objects with .zh and .en attributes (mutated in place).

    character_names: optional {speaker_label: character_name} (see
    db.list_characters) -- resolves each line's raw diarization label
    into an actual name shown to the translator, e.g. "[Xiaoling] 你好"
    instead of just "你好". Drives correct pronouns/honorifics/register,
    which the model previously had zero signal for. A line whose speaker
    has no name set is shown with no prefix at all, not the raw label
    (a diarization id like "SPEAKER_00" isn't a name and would just be
    noise for the model to ignore).

    save_cb: optional callback invoked after every batch (successful or
    not) with the full `lines` list so far, so progress survives a
    crash/network failure partway through -- call db.save_lines(...)
    from it. Without this, a failure on batch N loses batches 1..N-1
    too if the caller only saves once at the very end.

    usage_cb: optional callback invoked after every successful batch
    with (input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens) -- call db.log_usage(...) from it for cost
    tracking. input_tokens is the whole prompt; the cache counts are the
    parts of it read from / written to a provider prompt cache. In
    Reflect mode it's called once per LLM pass with just (input_tokens,
    output_tokens), so give the last two parameters defaults. Only fires
    for engines that report usage; pure-MT engines don't report token
    counts the same way, so nothing is logged for those.

    cost_cap_usd: stop cleanly once this run's estimated spend
    (estimate_cost_for_engine over each batch's real reported usage)
    reaches this many dollars. Checked after each batch, so the batch
    that crosses the cap still finishes and is saved -- the run can go
    over by at most one batch, and never loses finished work. cap_cb, if
    given, is called with the amount spent when the cap stops a run that
    still had batches left.

    target_ids: optional set of permanent line ids -- only those lines are
    translated (on top of the force_retranslate rule); every other line
    still serves as look-back/look-ahead context.

    A failed batch is retried once, then, if it fails again, its lines
    are left untranslated (.en stays empty) and noted in the returned
    `errors` list, rather than raising and losing everything after it.
    A batch whose engine returns the WRONG NUMBER of translations is
    treated the same way -- a real, confirmed bug this replaces: the
    old code paired the response with the batch by raw list position
    (zip()), so a response one line short (a common LLM failure: merging
    two lines into one, or silently dropping a line) silently shifted
    every translation after the gap onto the wrong line. A length
    mismatch is now always a batch error, never a misassignment --
    fixing the actual mismatch (an engine's own id-keyed JSON response
    parsing, for Claude/DeepSeek/Gemini/Ollama) lives in each engine's
    own translate_batch, since that's where the real per-line recovery
    (retry only the missing ones, not the whole batch) can happen with
    the id information still available; by the time a bare list[str]
    reaches here, all this can safely check is its length.

    By default, lines that already have non-empty .en are skipped --
    so calling this again after a partial failure only retries what's
    actually missing, instead of re-translating (and re-paying for)
    everything. Pass force_retranslate=True to redo everything anyway.

    context_window: how many already-translated lines immediately before
    each batch get shown to the model (as "how this was already
    translated", not something to retranslate). Batches are translated in
    isolation otherwise -- a pronoun or a person referred to only by
    relation ("her", "that guy") a few lines back has nothing to resolve
    against, and the model has to guess fresh every batch instead of
    staying consistent with what came right before it. 0 disables this.

    context_window_ahead: same idea, forward instead of back -- how many
    lines immediately AFTER this batch (raw, untranslated) get shown for
    context only. A line ending on a cliffhanger or an incomplete thought
    previously had nothing to resolve against going forward, only
    backward. 0 disables this.
    """
    target_lines = lines if force_retranslate else [ln for ln in lines if not ln.en.strip()]
    if target_ids is not None:
        target_lines = [ln for ln in target_lines if getattr(ln, "id", None) in target_ids]
    # Already English: nothing to translate, so it's carried over as-is
    # instead of spending a model call on it.
    english = [ln for ln in target_lines if is_english_line(ln)]
    for ln in english:
        ln.en = ln.zh
    target_lines = [ln for ln in target_lines if not is_english_line(ln)]
    if not target_lines:
        if english and save_cb:
            save_cb(lines)
        if detail_cb:
            detail_cb(1.0, "Nothing to translate")
        elif progress_cb:
            progress_cb(1.0)
        return lines, []

    character_names = character_names or {}
    context = build_translation_context(
        engine, drama_meta, style_note=style_note, novel_reference=novel_reference, locale=locale,
        glossary_terms=glossary_terms, style_guidelines=style_guidelines,
        ollama_num_ctx_override=ollama_num_ctx_override)
    errors = []
    stop_run = []
    spent = 0.0

    def record_usage(inp, out, cache_read=0, cache_write=0):
        nonlocal spent
        spent += estimate_cost_for_engine(engine, inp, out, cache_read, cache_write)
        if usage_cb:
            usage_cb(inp, out, cache_read, cache_write)

    batches = (plan_batches(target_lines, batch_size) if scene_aware_batches
               else [target_lines[i:i + batch_size]
                     for i in range(0, len(target_lines), batch_size)])
    n_batches = len(batches)
    last_frac = 0.0

    def report(frac, message):
        # Bisected retries replay a batch's passes; the bar must not go back.
        nonlocal last_frac
        last_frac = min(1.0, max(frac, last_frac))
        if detail_cb:
            detail_cb(last_frac, message)

    for bi, batch in enumerate(batches):
        if cancel_check_cb and cancel_check_cb():
            break
        batch_label = f"Batch {bi + 1} of {n_batches}"
        report(bi / n_batches, batch_label)
        first_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[0].idx)
        if context_window > 0:
            # Recomputed each batch (not just once outside the loop) since
            # more lines have been translated -- including by this very
            # loop -- by the time later batches run.
            preceding = lines[max(0, first_pos - context_window):first_pos]
            context["recent_context"] = [(ln.zh, ln.en) for ln in preceding if ln.en.strip()]
        if context_window_ahead > 0:
            # batch[-1]'s own position, not first_pos + len(batch) - 1 --
            # that assumed the batch is a contiguous slice of `lines`,
            # which isn't true when force_retranslate=False and an
            # already-translated line sits in the middle of what would
            # otherwise be contiguous target lines: target_lines skips it,
            # so the batch is shorter than the span it covers in `lines`.
            # The stale arithmetic could land back inside the batch itself,
            # showing the model a line as "upcoming" (don't translate this)
            # while also asking it to translate that same line right now.
            last_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[-1].idx)
            batch_idxs = {ln.idx for ln in batch}
            upcoming = lines[last_pos + 1:last_pos + 1 + context_window_ahead]
            context["upcoming_lines"] = [ln.zh for ln in upcoming
                                         if ln.zh.strip() and ln.idx not in batch_idxs]
        def _on_pass(pass_no, name, bi=bi, batch_label=batch_label):
            report((bi + (pass_no - 1) / len(REFLECT_PASSES)) / n_batches,
                   f"{batch_label}, pass {pass_no} of {len(REFLECT_PASSES)} ({name})")

        def _on_wait(delay, next_attempt, max_retries, batch_label=batch_label):
            # Fixed phrase and numbers only: nothing from the error text.
            report(last_frac, f"{batch_label} - Engine busy, waiting {delay:.0f} s to retry "
                              f"(attempt {next_attempt} of {max_retries})")

        def _translate_chunk(chunk):
            """Runs one translate attempt for chunk (the whole batch, or
            one bisected half of it). Returns
            (translations, critiques) -- critiques is None outside Reflect
            mode. A ContentModerationBlocked (or any other exception)
            propagates to the caller, which decides what to do about it."""
            chunk_context = dict(context)
            chunk_context["speaker_labels"] = [character_names.get(ln.speaker) for ln in chunk]
            chunk_context["line_ids"] = [getattr(ln, "id", None) for ln in chunk]
            chunk_context["batch_source_lines"] = [ln.zh for ln in chunk]
            chunk_context["line_languages"] = tagged_line_languages(chunk, context["source_language"])
            if reflect:
                return call_with_backoff(
                    lambda: reflect_translate_batch(engine, [ln.zh for ln in chunk], chunk_context,
                                                    usage_cb=record_usage, pass_cb=_on_pass))
            translations = call_with_backoff(
                lambda: engine.translate_batch([ln.zh for ln in chunk], chunk_context))
            if hasattr(engine, "last_usage"):
                u = engine.last_usage
                record_usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                             u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0))
            return translations, None

        def _process_chunk(chunk, allow_bisect):
            """Translates chunk, applying results directly onto the Line
            objects, or flagging/recording an error for whichever lines
            couldn't be translated. allow_bisect: whether a
            ContentModerationBlocked caught here should trigger one
            bisection retry (a bounded, one-level split --
            only True for the original, un-split batch, never for an
            already-bisected half)."""
            try:
                translations, critiques = _translate_chunk(chunk)
            except ContentModerationBlocked as blocked:
                if allow_bisect and len(chunk) > 1:
                    mid = len(chunk) // 2
                    _process_chunk(chunk[:mid], allow_bisect=False)
                    _process_chunk(chunk[mid:], allow_bisect=False)
                else:
                    for ln in chunk:
                        ln.flag = "content_blocked"
                        ln.flag_note = f"{blocked.engine}: {blocked.reason}"
                    errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                                   "error": f"blocked by {blocked.engine}'s content filter: "
                                            f"{blocked.reason}"})
                return
            except Exception as e:
                redacted = redact_secrets(str(e))
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                               "error": redacted})
                if isinstance(e, FreeTierDailyLimitReached):
                    stop_run.append(True)
                import applog
                applog.get_logger().error(f"translate batch {bi} failed: {redacted}")
                return
            if notes_cb and critiques is not None:
                notes = [{"line_idx": ln.idx, "term": "", "note_type": "reflection", "note": c}
                         for ln, c in zip(chunk, critiques) if c]
                if notes:
                    notes_cb(notes)
            if len(translations) != len(chunk):
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                               "error": f"engine returned {len(translations)} translation(s) for "
                                        f"{len(chunk)} line(s) -- left untranslated rather than "
                                        f"risk assigning a translation to the wrong line"})
            else:
                # request_translations_with_retry and the Reflect fallback pad
                # ids missing from the reply with "", so a cut-off reply still
                # matches in length; an empty result must not overwrite
                # anything or look like a success.
                empty = []
                for ln, tr in zip(chunk, translations):
                    if (tr or "").strip():
                        ln.en = tr
                    else:
                        empty.append(ln)
                if empty:
                    errors.append({"batch_index": bi, "lines": [ln.idx for ln in empty],
                                   "error": f"{len(empty)} of {len(chunk)} lines got no "
                                            f"translation (reply may have been cut off; "
                                            f"try a smaller batch size)"})

        wait_token = _backoff_wait_var.set(_on_wait if detail_cb else None)
        try:
            _process_chunk(batch, allow_bisect=True)
        finally:
            _backoff_wait_var.reset(wait_token)
        if save_cb:
            save_cb(lines)
        if detail_cb:
            # No "done" message for a batch cut short by a cancel.
            if not (cancel_check_cb and cancel_check_cb()):
                report((bi + 1) / n_batches, f"{batch_label} done")
        elif progress_cb:
            progress_cb((bi + 1) / n_batches)
        if stop_run:
            break
        if isinstance(engine, FallbackEngine) and engine.cap_exhausted() and bi + 1 < n_batches:
            if cap_cb:
                cap_cb(engine.spent[engine.active])
            break
        if cost_cap_usd is not None and spent >= cost_cap_usd and bi + 1 < n_batches:
            if cap_cb:
                cap_cb(spent)
            break
    return lines, errors


def translate_lines_with_engine(*args, **kwargs):
    """Runs _translate_lines_with_engine with the job's cancel check
    visible to the backoff and throttle waits (see _cancellable_sleep)."""
    # Bind against the real signature so cancel_check_cb is found whether
    # the caller passed it positionally or by keyword; the context var lets
    # sleeps deep in shared.py see it without threading it through every call.
    bound = inspect.signature(_translate_lines_with_engine).bind(*args, **kwargs)
    token = _cancel_check_var.set(bound.arguments.get("cancel_check_cb"))
    try:
        return _translate_lines_with_engine(*args, **kwargs)
    finally:
        _cancel_check_var.reset(token)
