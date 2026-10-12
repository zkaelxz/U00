"""Reflect mode and the per-run translate loop."""

import inspect
import sentence_groups
from .engine_registry import default_batch_size, default_reply_guard
from .fallback import FallbackEngine
from .llm_tasks import (bounded_llm_calls, call_batch_bounded,
                        llm_tasks_scope_active, request_deadline_for)
from .local import scrub_error_text
from .pricing import estimate_cost_for_engine
from .run_limits import FailureStreak, RateLimitWaitBudget, _rate_limit_budget_var
from .prompts import (MALE_PRONOUN_WORD, is_male_speaker_label, known_male_names,
                      pronoun_default_active, pronoun_neutral_texts)
from .reflect_pass import REFLECT_PASSES, reflect_translate_batch
from .thinking import title_thinking
from .translation_guard import FLAG_NOTE as MERGE_SHIFT_FLAG_NOTE
from .translation_guard import allowed_source_runs, flag_unusable_replies, guard_batch
from .shared import (
    ContentModerationBlocked,
    _backoff_wait_var,
    _cancel_check_var,
    call_with_backoff,
    is_english_line,
    tagged_line_languages,
    redact_secrets,
    TranslationCancelled,
    stops_translate_run,
)
import source_change


def build_translation_context(engine, drama_meta: dict, style_note: str = "", novel_reference=None,
                              locale: str = "en-US", glossary_terms=None, style_guidelines: str = "",
                              ollama_num_ctx_override: int = None, thinking: bool = None) -> dict:
    """The per-job context every engine's prompt is built from -- shared by
    the batch paths (runs, bulk submission, Compare, stronger engine, Manhua)
    so they send the same prompt. Live builds its own per-chunk context
    (live_cue_translation.build_live_context).

    thinking: let a model that can reason do so. None means the title's
    remembered choice, else off, so a run is fast and cheap by default;
    engines without a request switch ignore it."""
    if thinking is None:
        thinking = title_thinking(drama_meta)
    return {
        "reply_without_thinking": not thinking,
        "reply_with_thinking": bool(thinking),
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


PRONOUN_CHECK_FLAG = "pronoun_check"


def _record_engine_usage(engine, record_usage):
    if hasattr(engine, "last_usage"):
        u = engine.last_usage
        record_usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                     u.get("cache_read_tokens", 0), u.get("cache_write_tokens", 0))


def _recheck_male_pronouns(engine, lines, translated, context, character_names, batch_size,
                           save_cb, record_usage, cap_reached, cancel_check_cb):
    """With the she/her default on, asks once more for the lines it was meant to
    prevent: an English line saying he/him/his for a speaker with no he/him
    character. Only those ids go back, with a stricter instruction; a line still
    male afterwards is flagged for Review (flag/flag_note, which the finishing
    step saves on its own) rather than rewritten blind. The retry is bounded by
    the run's cost cap and cancel, and a failed retry just leaves the flag."""
    guidelines = context.get("style_guidelines") or ""
    if (not pronoun_default_active(guidelines) or context.get("source_language", "zh") != "zh"
            or not getattr(engine, "supports_reference", False)):
        return
    known_male = [n.casefold() for n in known_male_names(guidelines)]

    def suspect(ln):
        if not MALE_PRONOUN_WORD.search(ln.en or "") or is_male_speaker_label(character_names.get(ln.speaker)):
            return False
        # A line naming a known male character may be about him, not a slip.
        text = f"{ln.zh}\n{ln.en}".casefold()
        return not any(name in text for name in known_male)

    suspects = [ln for ln in translated if suspect(ln)]
    strict = dict(context, pronoun_strict=True)
    retried = []
    for start in range(0, len(suspects), batch_size):
        if cap_reached() or (cancel_check_cb and cancel_check_cb()):
            break
        chunk = suspects[start:start + batch_size]
        chunk_context = dict(strict)
        chunk_context["speaker_labels"] = [character_names.get(ln.speaker) for ln in chunk]
        chunk_context["line_ids"] = [getattr(ln, "id", None) for ln in chunk]
        chunk_context["batch_source_lines"] = [ln.zh for ln in chunk]
        chunk_context["line_languages"] = tagged_line_languages(chunk, context["source_language"])
        sources = pronoun_neutral_texts(context, [ln.zh for ln in chunk])
        try:
            redone = call_with_backoff(
                lambda: call_batch_bounded(
                    engine, lambda: engine.translate_batch(sources, chunk_context),
                    on_late_result=lambda: _record_engine_usage(engine, record_usage)))
            _record_engine_usage(engine, record_usage)
        except Exception as e:
            import applog
            applog.get_logger().warning(f"pronoun re-check skipped a batch: {redact_secrets(str(e))}")
            continue
        if len(redone) != len(chunk):
            continue
        for ln, tr in zip(chunk, redone):
            if (tr or "").strip():
                ln.en = tr
                retried.append(ln)
    if retried and save_cb:
        save_cb(lines)
    for ln in suspects:
        if suspect(ln) and not ln.flag:
            ln.flag = PRONOUN_CHECK_FLAG
            ln.flag_note = ("Says he/him but no he/him character is set for this speaker; "
                            "check the pronoun")


def _translate_lines_with_engine(lines, engine, drama_meta: dict, batch_size: int = None,
                                 style_note: str = "", novel_reference=None, progress_cb=None,
                                 save_cb=None, force_retranslate: bool = False,
                                 locale: str = "en-US", glossary_terms=None, usage_cb=None,
                                 style_guidelines: str = "", cancel_check_cb=None,
                                 context_window: int = 6, context_window_ahead: int = 3,
                                 character_names: dict = None, ollama_num_ctx_override: int = None,
                                 reflect: bool = False, notes_cb=None, cost_cap_usd: float = None,
                                 cap_cb=None, target_ids=None, detail_cb=None,
                                 scene_aware_batches: bool = False, thinking: bool = None,
                                 translate_by_sentence=None, check_replies: bool = None,
                                 scene_break_gap: float = SCENE_BREAK_GAP_SECONDS,
                                 sentence_limits: sentence_groups.GroupLimits = sentence_groups.DEFAULT_LIMITS):
    """thinking: ask engines with a switch (DeepSeek, Ollama) to reason before
    answering; None = the title's remembered choice, else off. Its reasoning is
    never saved with a line.

    scene_aware_batches: start batches at scene breaks (plan_batches)
    instead of cutting fixed slices of batch_size. Same maximum size.
    scene_break_gap: the pause, in seconds, that counts as a scene break.
    sentence_limits: how much translate_by_sentence may join into one sentence.

    translate_by_sentence: show the model each run of timed fragments that
    makes one sentence as that whole sentence, and put its English back onto
    the fragments (sentence_groups). None means the title's saved choice
    (drama_meta["translate_by_sentence"]), so every translate path agrees.
    Inert for Reflect mode and for engines that don't follow instructions.

    check_replies: run the merged/shifted-reply guard. None = the engine
    and model's default (default_reply_guard); an explicit True/False wins.
    An engine with checks_replies = False is never checked.

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
        source_change.clear_for_new_english(ln)
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
        ollama_num_ctx_override=ollama_num_ctx_override, thinking=thinking)
    context["check_replies"] = check_replies
    errors = []
    stop_run = []
    translated = []
    streak = FailureStreak()
    hard_failures = []
    spent = 0.0
    allowed_runs = allowed_source_runs(glossary_terms, style_guidelines)

    def record_usage(inp, out, cache_read=0, cache_write=0):
        nonlocal spent
        spent += estimate_cost_for_engine(engine, inp, out, cache_read, cache_write)
        if usage_cb:
            usage_cb(inp, out, cache_read, cache_write)

    size = batch_size or default_batch_size(engine)
    guard_enabled = default_reply_guard(engine) if check_replies is None else bool(check_replies)
    batches = (plan_batches(target_lines, size, scene_break_gap) if scene_aware_batches
               else [target_lines[i:i + size]
                     for i in range(0, len(target_lines), size)])
    if translate_by_sentence is None:
        translate_by_sentence = bool((drama_meta or {}).get("translate_by_sentence"))
    sentence_mode = (translate_by_sentence and not reflect
                     and getattr(engine, "supports_reference", False))
    groups = (sentence_groups.group_fragments(target_lines, lines, sentence_limits)
              if sentence_mode else [])
    if groups:
        batches = sentence_groups.align_batches_to_groups(batches, groups)
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

        def _context_for(chunk):
            chunk_context = dict(context)
            chunk_context["speaker_labels"] = [character_names.get(ln.speaker) for ln in chunk]
            chunk_context["line_ids"] = [getattr(ln, "id", None) for ln in chunk]
            chunk_context["batch_source_lines"] = [ln.zh for ln in chunk]
            chunk_context["line_languages"] = tagged_line_languages(chunk, context["source_language"])
            return chunk_context

        def _call_engine(chunk, chunk_context):
            sources = pronoun_neutral_texts(context, [ln.zh for ln in chunk])
            # A batch abandoned on Cancel or at the deadline was still billed, so
            # its usage is logged when the request finally returns.
            translations = call_with_backoff(
                lambda: call_batch_bounded(
                    engine, lambda: engine.translate_batch(sources, chunk_context),
                    on_late_result=lambda: _record_engine_usage(engine, record_usage)))
            _record_engine_usage(engine, record_usage)
            return translations

        def _settle_sentences(chunk, translations, chunk_groups):
            """Gives each fragment of a sentence group its English (see
            sentence_groups.settle_group); fragments of a group the model's
            reply can't be used for are translated the normal way, in one
            extra request, so the mode never leaves a blank the plain path
            wouldn't."""
            if len(translations) != len(chunk):
                return translations  # reported as a length mismatch by the caller
            position = {id(ln): p for p, ln in enumerate(chunk)}
            out, redo = list(translations), []
            for group in chunk_groups:
                where = [position[id(ln)] for ln in group]
                settled = sentence_groups.settle_group(
                    [ln.zh for ln in group], [out[p] for p in where])
                if settled is None:
                    redo.extend(where)
                else:
                    for p, text in zip(where, settled):
                        out[p] = text
            if redo:
                redo_lines = [chunk[p] for p in redo]
                for p, text in zip(redo, _call_engine(redo_lines, _context_for(redo_lines))):
                    out[p] = text
            return out

        def _translate_chunk(chunk):
            """Runs one translate attempt for chunk (the whole batch, or
            one bisected half of it). Returns
            (translations, critiques) -- critiques is None outside Reflect
            mode. A ContentModerationBlocked (or any other exception)
            propagates to the caller, which decides what to do about it."""
            chunk_context = _context_for(chunk)
            if reflect:
                sources = pronoun_neutral_texts(context, [ln.zh for ln in chunk])
                return call_with_backoff(
                    lambda: reflect_translate_batch(engine, sources, chunk_context,
                                                    usage_cb=record_usage, pass_cb=_on_pass))
            chunk_groups = sentence_groups.complete_groups(chunk, groups)
            if chunk_groups:
                position = {id(ln): p for p, ln in enumerate(chunk)}
                chunk_context["sentence_groups"] = [[position[id(ln)] for ln in g]
                                                    for g in chunk_groups]
            translations = _call_engine(chunk, chunk_context)
            if chunk_groups:
                translations = _settle_sentences(chunk, translations, chunk_groups)
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
                    # The first half may have ended on Cancel or a deadline, which
                    # it swallows; the second half must not send another request.
                    if stop_run or (cancel_check_cb and cancel_check_cb()):
                        return
                    _process_chunk(chunk[mid:], allow_bisect=False)
                else:
                    for ln in chunk:
                        ln.flag = "content_blocked"
                        ln.flag_note = f"{blocked.engine}: {blocked.reason}"
                    errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                                   "error": f"blocked by {blocked.engine}'s content filter: "
                                            f"{blocked.reason}"})
                return
            except TranslationCancelled:
                # Not a failure of the batch; the loop's cancel check ends the run.
                return
            except Exception as e:
                redacted = redact_secrets(str(e))
                hard_failures.append(e)
                errors.append({"batch_index": bi, "lines": [ln.idx for ln in chunk],
                               "error": redacted})
                # After a timeout the abandoned request may still be running, so
                # the next batch would be refused or double-billed.
                if stops_translate_run(e):
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
                empty, fresh = [], []
                for ln, tr in zip(chunk, translations):
                    if (tr or "").strip():
                        ln.en = tr
                        source_change.clear_for_new_english(ln)
                        translated.append(ln)
                        fresh.append(ln)
                    else:
                        empty.append(ln)
                flag_unusable_replies(empty, getattr(engine, "last_rejected", None))
                if empty:
                    errors.append({"batch_index": bi, "lines": [ln.idx for ln in empty],
                                   "error": f"{len(empty)} of {len(chunk)} lines got no "
                                            f"translation (reply may have been cut off; "
                                            f"try a smaller batch size)"})
                # Sentence mode redistributes one sentence over its fragments, so
                # fragment lengths say nothing there.
                if (allow_bisect and not reflect and not groups
                        and getattr(engine, "checks_replies", True) and guard_enabled):
                    # Only this attempt's replies: a line it left out still holds its
                    # old English (maybe the user's edit), which must not be re-asked or flagged.
                    _guard_merged_or_shifted(fresh)

        def _guard_merged_or_shifted(chunk):
            def stop():
                return bool(stop_run or (cancel_check_cb and cancel_check_cb())
                            or (cost_cap_usd is not None and spent >= cost_cap_usd)
                            or (isinstance(engine, FallbackEngine) and engine.cap_exhausted()))

            def redo(lines_to_redo):
                try:
                    return _call_engine(lines_to_redo, _context_for(lines_to_redo))
                except TranslationCancelled:
                    return None
                except Exception as e:
                    redacted = redact_secrets(str(e))
                    if stops_translate_run(e):
                        # The run ends here, so the reason must reach the job's errors.
                        stop_run.append(True)
                        errors.append({"batch_index": bi, "lines": [ln.idx for ln in lines_to_redo],
                                       "error": redacted})
                    import applog
                    applog.get_logger().warning(f"merge/shift re-ask skipped for batch {bi}: {redacted}")
                    return None

            for ln, _reason in guard_batch(chunk, redo, stop, allowed_runs):
                if not ln.flag:
                    ln.flag = "uncertain_translation"
                    ln.flag_note = MERGE_SHIFT_FLAG_NOTE

        wait_token = _backoff_wait_var.set(_on_wait if detail_cb else None)
        hard_failures.clear()
        translated_before = len(translated)
        try:
            _process_chunk(batch, allow_bisect=True)
        finally:
            _backoff_wait_var.reset(wait_token)
        if streak.record(failed=bool(hard_failures), succeeded=len(translated) > translated_before) \
                and not stop_run:
            stop_run.append(True)
            errors.append({"batch_index": bi, "lines": [ln.idx for ln in batch],
                           "error": streak.stop_message(scrub_error_text(str(hard_failures[-1])))})
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
    else:
        if not stop_run and not (cancel_check_cb and cancel_check_cb()):
            _recheck_male_pronouns(
                engine, lines, translated, context, character_names, size, save_cb,
                record_usage, lambda: cost_cap_usd is not None and spent >= cost_cap_usd,
                cancel_check_cb)
    return lines, errors


def translate_lines_with_engine(*args, **kwargs):
    """Runs _translate_lines_with_engine with the job's cancel check
    visible to the backoff and throttle waits (see _cancellable_sleep)."""
    # Bind against the real signature so cancel_check_cb is found whether
    # the caller passed it positionally or by keyword; the context var lets
    # sleeps deep in shared.py see it without threading it through every call.
    bound = inspect.signature(_translate_lines_with_engine).bind(*args, **kwargs)
    cancel_check = bound.arguments.get("cancel_check_cb")
    token = _cancel_check_var.set(cancel_check)
    budget_token = _rate_limit_budget_var.set(RateLimitWaitBudget())
    try:
        if llm_tasks_scope_active():
            return _translate_lines_with_engine(*args, **kwargs)
        # One run per drama at a time, so the drama keys the rule that an
        # abandoned request blocks a new one; without an id the global cap applies.
        drama_id = (bound.arguments.get("drama_meta") or {}).get("id")
        engine = bound.arguments["engine"]
        # Reflect passes go through call_llm_json one request at a time, and
        # with thinking on DeepSeek's reply can legitimately take the whole
        # client timeout; the default DeepSeek deadline would cut it short.
        with bounded_llm_calls(f"translate:{drama_id}" if drama_id else None,
                               cancel_check or (lambda: False), lenient_empty=True,
                               deadline=request_deadline_for(engine)):
            return _translate_lines_with_engine(*args, **kwargs)
    finally:
        _rate_limit_budget_var.reset(budget_token)
        _cancel_check_var.reset(token)
