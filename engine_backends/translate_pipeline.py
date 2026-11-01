"""The per-run translate loop."""

import inspect
import types
import weakref
import sentence_groups
from .engine_registry import default_batch_size, default_reply_guard, novel_batch_chars
from . import glossary_check
from .fallback import FallbackEngine
from .novel_batches import chapter_start_idxs, novel_paragraph_ends, plan_paragraph_batches
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
    LLMTaskTimeout,
    TranslationCancelled,
    stops_translate_run,
)
import source_change


def build_translation_context(engine, drama_meta: dict, style_note: str = "", novel_reference=None,
                              locale: str = "en-US", glossary_terms=None, style_guidelines: str = "",
                              ollama_num_ctx_override: int = None, thinking: bool = None,
                              temperature: float = None) -> dict:
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
        "temperature": temperature,
    }


# Recorded with each translated line (line_provenance).
# Bump it whenever the translate prompt or its batching changes meaning.
TRANSLATE_PROMPT_VERSION = "4"


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


def _ids(lines) -> list:
    """Permanent line ids for a failure record: indexes are renumbered by a merge
    or split, ids are not."""
    return [ln.id for ln in lines if getattr(ln, "id", None) is not None]


# The usage dict last recorded per engine: a failed attempt that died before
# the engine reset last_usage still holds the previous call's numbers.
_recorded_usage = weakref.WeakKeyDictionary()


def _record_failed_attempt_usage(engine, record_usage):
    """Tokens billed by an attempt that then failed (a later request of the same
    batch hit a 429 or 500, and the retry runs the whole batch again), so the
    spend and the cap count them. A fallback chain reports these itself."""
    if hasattr(getattr(engine, "unwrapped_engine", engine), "failed_usage_cb"):
        return
    if getattr(engine, "last_usage", None) is not _recorded_usage.get(engine):
        _record_engine_usage(engine, record_usage)


def _record_engine_usage(engine, record_usage):
    if hasattr(engine, "last_usage"):
        u = engine.last_usage
        try:
            _recorded_usage[engine] = u
        except TypeError:
            pass  # not weak-referenceable: a failed attempt is then recorded as usual
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
            if stops_translate_run(e):
                if retried and save_cb:
                    save_cb(lines)
                raise
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
                                 sentence_limits: sentence_groups.GroupLimits = sentence_groups.DEFAULT_LIMITS,
                                 stop_before_batch=None, temperature: float = None):
    """Translates `lines` in place and returns (lines, errors); see _TranslateRun
    for what each argument does."""
    # The signature stays the one place the arguments are listed: the public
    # wrapper binds against it, and _TranslateRun reads them from this namespace.
    return _TranslateRun(types.SimpleNamespace(**locals())).run()


class _TranslateRun:
    """The per-run translate loop. What each argument of _translate_lines_with_engine
    does is in docs/engine-backends.md, "Arguments of the translate pipeline".
    """

    def __init__(self, args):
        self.args = args
        self.lines = args.lines
        self.engine = args.engine
        self.errors = []
        self.stop_run = False
        self.translated = []
        self.streak = FailureStreak()
        self.hard_failures = []
        self.spent = 0.0
        self.last_frac = 0.0
        self.bi = 0
        self.batch_label = ""
        self.groups = []
        self.character_names = args.character_names or {}
        self.allowed_runs = allowed_source_runs(args.glossary_terms, args.style_guidelines)
        # A local model works through a long system prompt unevenly, so for it the
        # names go beside the lines of each batch as well.
        self.glossary_first = getattr(self.engine, "name", "") == "ollama"

    def run(self):
        a = self.args
        target_lines = self._lines_to_translate()
        if not target_lines:
            return self.lines, []
        self.context = build_translation_context(
            self.engine, a.drama_meta, style_note=a.style_note, novel_reference=a.novel_reference,
            locale=a.locale, glossary_terms=a.glossary_terms, style_guidelines=a.style_guidelines,
            ollama_num_ctx_override=a.ollama_num_ctx_override, thinking=a.thinking,
            temperature=a.temperature)
        self.context["check_replies"] = a.check_replies
        self.size = a.batch_size or default_batch_size(self.engine)
        self.guard_enabled = (default_reply_guard(self.engine) if a.check_replies is None
                              else bool(a.check_replies))
        batches = self._plan(target_lines)
        self.n_batches = len(batches)
        if self._run_batches(batches):
            self._recheck_pronouns()
        return self.lines, self.errors

    def _lines_to_translate(self):
        """The lines this run translates. Lines that are already English are
        carried over as-is instead of spending a model call on them. An empty
        result has already been reported as finished."""
        a = self.args
        target_lines = (self.lines if a.force_retranslate
                        else [ln for ln in self.lines if not ln.en.strip()])
        if a.target_ids is not None:
            target_lines = [ln for ln in target_lines if getattr(ln, "id", None) in a.target_ids]
        english = [ln for ln in target_lines if is_english_line(ln)]
        for ln in english:
            ln.en = ln.zh
            source_change.clear_for_new_english(ln)
        target_lines = [ln for ln in target_lines if not is_english_line(ln)]
        if not target_lines:
            if english and a.save_cb:
                a.save_cb(self.lines)
            if a.detail_cb:
                a.detail_cb(1.0, "Nothing to translate")
            elif a.progress_cb:
                a.progress_cb(1.0)
        return target_lines

    def _plan(self, target_lines):
        a = self.args
        size = self.size
        novel_ends = novel_paragraph_ends(a.drama_meta, self.lines)
        chapter_starts = chapter_start_idxs(self.lines) if novel_ends else ()
        batches = (plan_paragraph_batches(target_lines, size, novel_ends,
                                          max_chars=None if a.batch_size else novel_batch_chars(self.engine),
                                          chapter_starts=chapter_starts) if novel_ends
                   else plan_batches(target_lines, size, a.scene_break_gap) if a.scene_aware_batches
                   else [target_lines[i:i + size] for i in range(0, len(target_lines), size)])
        by_sentence = a.translate_by_sentence
        if by_sentence is None:
            by_sentence = bool((a.drama_meta or {}).get("translate_by_sentence"))
        if (by_sentence and not a.reflect
                and getattr(self.engine, "supports_reference", False)):
            self.groups = sentence_groups.group_fragments(target_lines, self.lines, a.sentence_limits)
        if self.groups:
            batches = sentence_groups.align_batches_to_groups(batches, self.groups)
        return batches

    def _run_batches(self, batches) -> bool:
        """False when the run was stopped before every batch had its turn."""
        a = self.args
        for self.bi, batch in enumerate(batches):
            if (a.cancel_check_cb and a.cancel_check_cb()) or (a.stop_before_batch and a.stop_before_batch()):
                return False
            self.batch_label = f"Batch {self.bi + 1} of {self.n_batches}"
            self._report(self.bi / self.n_batches, self.batch_label)
            self._set_batch_context(batch)
            self._translate_batch(batch)
            self._finish_batch(batch)
            if self.stop_run or self._cap_stops_run():
                return False
        return True

    def _report(self, frac, message):
        # Bisected retries replay a batch's passes; the bar must not go back.
        self.last_frac = min(1.0, max(frac, self.last_frac))
        if self.args.detail_cb:
            self.args.detail_cb(self.last_frac, message)

    def _set_batch_context(self, batch):
        a = self.args
        lines = self.lines
        first_pos = next(i for i, ln in enumerate(lines) if ln.idx == batch[0].idx)
        if a.context_window > 0:
            # Recomputed each batch (not just once outside the loop) since
            # more lines have been translated -- including by this very
            # loop -- by the time later batches run.
            preceding = lines[max(0, first_pos - a.context_window):first_pos]
            self.context["recent_context"] = [(ln.zh, ln.en) for ln in preceding if ln.en.strip()]
        if a.context_window_ahead > 0:
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
            upcoming = lines[last_pos + 1:last_pos + 1 + a.context_window_ahead]
            self.context["upcoming_lines"] = [ln.zh for ln in upcoming
                                              if ln.zh.strip() and ln.idx not in batch_idxs]

    def _record_usage(self, inp, out, cache_read=0, cache_write=0):
        self.spent += estimate_cost_for_engine(self.engine, inp, out, cache_read, cache_write)
        if self.args.usage_cb:
            self.args.usage_cb(inp, out, cache_read, cache_write)

    def _on_pass(self, pass_no, name):
        self._report((self.bi + (pass_no - 1) / len(REFLECT_PASSES)) / self.n_batches,
                     f"{self.batch_label}, pass {pass_no} of {len(REFLECT_PASSES)} ({name})")

    def _on_wait(self, delay, next_attempt, max_retries):
        # Fixed phrase and numbers only: nothing from the error text.
        self._report(self.last_frac, f"{self.batch_label} - Engine busy, waiting {delay:.0f} s to retry "
                                     f"(attempt {next_attempt} of {max_retries})")

    def _context_for(self, chunk):
        chunk_context = dict(self.context)
        chunk_context["speaker_labels"] = [self.character_names.get(ln.speaker) for ln in chunk]
        chunk_context["line_ids"] = [getattr(ln, "id", None) for ln in chunk]
        chunk_context["batch_source_lines"] = [ln.zh for ln in chunk]
        chunk_context["line_languages"] = tagged_line_languages(chunk, self.context["source_language"])
        if self.args.glossary_terms and self.glossary_first:
            chunk_context = glossary_check.tag_batch(chunk_context, chunk, self.args.glossary_terms)
        return chunk_context

    def _call_engine(self, chunk, chunk_context):
        engine = self.engine
        sources = pronoun_neutral_texts(self.context, [ln.zh for ln in chunk])

        # A batch abandoned on Cancel or at the deadline was still billed, so
        # its usage is logged when the request finally returns.
        def attempt():
            try:
                return call_batch_bounded(
                    engine, lambda: engine.translate_batch(sources, chunk_context),
                    on_late_result=lambda: _record_engine_usage(engine, self._record_usage))
            except (TranslationCancelled, LLMTaskTimeout):
                raise  # the late-result hook logs an abandoned request
            except Exception:
                _record_failed_attempt_usage(engine, self._record_usage)
                raise

        translations = call_with_backoff(attempt)
        _record_engine_usage(engine, self._record_usage)
        return translations

    def _settle_sentences(self, chunk, translations, chunk_groups):
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
            for p, text in zip(redo, self._call_engine(redo_lines, self._context_for(redo_lines))):
                out[p] = text
        return out

    def _translate_chunk(self, chunk):
        """Runs one translate attempt for chunk (the whole batch, or
        one bisected half of it). Returns
        (translations, critiques) -- critiques is None outside Reflect
        mode. A ContentModerationBlocked (or any other exception)
        propagates to the caller, which decides what to do about it."""
        chunk_context = self._context_for(chunk)
        if self.args.reflect:
            sources = pronoun_neutral_texts(self.context, [ln.zh for ln in chunk])
            finished_passes = {}  # a retry must not pay again for a pass that finished
            return call_with_backoff(
                lambda: reflect_translate_batch(self.engine, sources, chunk_context,
                                                usage_cb=self._record_usage, pass_cb=self._on_pass,
                                                resume=finished_passes))
        chunk_groups = sentence_groups.complete_groups(chunk, self.groups)
        if chunk_groups:
            position = {id(ln): p for p, ln in enumerate(chunk)}
            chunk_context["sentence_groups"] = [[position[id(ln)] for ln in g]
                                                for g in chunk_groups]
        translations = self._call_engine(chunk, chunk_context)
        if chunk_groups:
            translations = self._settle_sentences(chunk, translations, chunk_groups)
        return translations, None

    def _add_error(self, lines, error):
        self.errors.append({"batch_index": self.bi, "lines": [ln.idx for ln in lines],
                            "line_ids": _ids(lines), "error": error})

    def _process_chunk(self, chunk, allow_bisect):
        """Translates chunk, applying results directly onto the Line
        objects, or flagging/recording an error for whichever lines
        couldn't be translated. allow_bisect: whether a
        ContentModerationBlocked caught here should trigger one
        bisection retry (a bounded, one-level split --
        only True for the original, un-split batch, never for an
        already-bisected half)."""
        try:
            translations, critiques = self._translate_chunk(chunk)
        except ContentModerationBlocked as blocked:
            if allow_bisect and len(chunk) > 1:
                mid = len(chunk) // 2
                self._process_chunk(chunk[:mid], allow_bisect=False)
                # The first half may have ended on Cancel or a deadline, which
                # it swallows; the second half must not send another request.
                if self.stop_run or (self.args.cancel_check_cb and self.args.cancel_check_cb()):
                    return
                self._process_chunk(chunk[mid:], allow_bisect=False)
            else:
                for ln in chunk:
                    ln.flag = "content_blocked"
                    ln.flag_note = f"{blocked.engine}: {blocked.reason}"
                self._add_error(chunk, f"blocked by {blocked.engine}'s content filter: "
                                       f"{blocked.reason}")
            return
        except TranslationCancelled:
            # Not a failure of the batch; the loop's cancel check ends the run.
            return
        except Exception as e:
            redacted = redact_secrets(str(e))
            self.hard_failures.append(e)
            self._add_error(chunk, redacted)
            # After a timeout the abandoned request may still be running, so
            # the next batch would be refused or double-billed.
            if stops_translate_run(e):
                self.stop_run = True
            import applog
            applog.get_logger().error(f"translate batch {self.bi} failed: {redacted}")
            return
        self._apply_translations(chunk, translations, critiques, allow_bisect)

    def _apply_translations(self, chunk, translations, critiques, allow_bisect):
        if self.args.notes_cb and critiques is not None:
            notes = [{"line_idx": ln.idx, "term": "", "note_type": "reflection", "note": c}
                     for ln, c in zip(chunk, critiques) if c]
            if notes:
                self.args.notes_cb(notes)
        if len(translations) != len(chunk):
            self._add_error(chunk, f"engine returned {len(translations)} translation(s) for "
                                   f"{len(chunk)} line(s) -- left untranslated rather than "
                                   f"risk assigning a translation to the wrong line")
            return
        # request_translations_with_retry and the Reflect fallback pad
        # ids missing from the reply with "", so a cut-off reply still
        # matches in length; an empty result must not overwrite
        # anything or look like a success.
        empty, fresh = [], []
        for ln, tr in zip(chunk, translations):
            if (tr or "").strip():
                ln.en = tr
                source_change.clear_for_new_english(ln)
                self.translated.append(ln)
                fresh.append(ln)
            else:
                empty.append(ln)
        if empty and (len(chunk) > 1 or len(empty) < len(chunk)):
            empty = self._retry_smaller(empty, fresh, depth=0)
        flag_unusable_replies(empty, getattr(self.engine, "last_rejected", None))
        if empty:
            self._add_error(empty, f"{len(empty)} of {len(chunk)} lines got no "
                                   f"translation (reply may have been cut off; "
                                   f"try a smaller batch size)")
        # Sentence mode redistributes one sentence over its fragments, so
        # fragment lengths say nothing there.
        if (allow_bisect and not self.args.reflect and not self.groups
                and getattr(self.engine, "checks_replies", True) and self.guard_enabled):
            # Only this attempt's replies: a line it left out still holds its
            # old English (maybe the user's edit), which must not be re-asked or flagged.
            self._guard_merged_or_shifted(fresh)

    _EMPTY_RETRY_DEPTH = 2

    def _retry_smaller(self, empty, fresh, depth):
        """Re-asks lines a reply left empty in halves, so a cut-off reply on a
        big batch doesn't leave a hole until the user reruns by hand. Bounded
        by depth; returns the lines that are still empty."""
        if not empty or depth >= self._EMPTY_RETRY_DEPTH:
            return empty
        parts = [empty] if len(empty) == 1 else [empty[:len(empty) // 2], empty[len(empty) // 2:]]
        still = []
        for part in parts:
            if self._should_stop():
                still.extend(part)
                continue
            try:
                translations, _ = self._translate_chunk(part)
            except Exception:
                # The first attempt's gap is what gets reported; a failed retry adds nothing.
                still.extend(part)
                continue
            if len(translations) != len(part):
                still.extend(part)
                continue
            missed = []
            for ln, tr in zip(part, translations):
                if (tr or "").strip():
                    ln.en = tr
                    source_change.clear_for_new_english(ln)
                    self.translated.append(ln)
                    fresh.append(ln)
                else:
                    missed.append(ln)
            still.extend(self._retry_smaller(missed, fresh, depth + 1) if len(part) > 1 else missed)
        return still

    def _should_stop(self):
        a = self.args
        return bool(self.stop_run or (a.cancel_check_cb and a.cancel_check_cb())
                    or (a.stop_before_batch and a.stop_before_batch())
                    or (a.cost_cap_usd is not None and self.spent >= a.cost_cap_usd)
                    or (isinstance(self.engine, FallbackEngine) and self.engine.cap_exhausted()))

    def _check_glossary(self, chunk):
        engine = self.engine

        def ask(lines_to_redo, pairs):
            redo_context = dict(self._context_for(lines_to_redo), glossary_required=pairs,
                                glossary_correction=True)
            sources = pronoun_neutral_texts(self.context, [ln.zh for ln in lines_to_redo])
            try:
                out = call_with_backoff(
                    lambda: call_batch_bounded(
                        engine, lambda: engine.translate_batch(sources, redo_context),
                        on_late_result=lambda: _record_engine_usage(engine, self._record_usage)))
                _record_engine_usage(engine, self._record_usage)
                return out
            except TranslationCancelled:
                return None
            except Exception as e:
                redacted = redact_secrets(str(e))
                if stops_translate_run(e):
                    # These lines hold this run's English; only the reason is recorded.
                    self.stop_run = True
                    self.errors.append({"batch_index": self.bi, "lines": [], "error": redacted})
                import applog
                applog.get_logger().warning(f"glossary re-ask skipped for batch {self.bi}: {redacted}")
                return None

        glossary_check.enforce(chunk, self.args.glossary_terms, ask, self._should_stop,
                               self.allowed_runs)

    def _guard_merged_or_shifted(self, chunk):
        def redo(lines_to_redo):
            try:
                return self._call_engine(lines_to_redo, self._context_for(lines_to_redo))
            except TranslationCancelled:
                return None
            except Exception as e:
                redacted = redact_secrets(str(e))
                if stops_translate_run(e):
                    # The run ends here, so the reason must reach the job's errors.
                    self.stop_run = True
                    self._add_error(lines_to_redo, redacted)
                import applog
                applog.get_logger().warning(f"merge/shift re-ask skipped for batch {self.bi}: {redacted}")
                return None

        for ln, _reason in guard_batch(chunk, redo, self._should_stop, self.allowed_runs):
            if not ln.flag:
                ln.flag = "uncertain_translation"
                ln.flag_note = MERGE_SHIFT_FLAG_NOTE

    def _translate_batch(self, batch):
        wait_token = _backoff_wait_var.set(self._on_wait if self.args.detail_cb else None)
        self.hard_failures.clear()
        translated_before = len(self.translated)
        try:
            self._process_chunk(batch, allow_bisect=True)
            if self.args.glossary_terms and len(self.translated) > translated_before:
                self._check_glossary(self.translated[translated_before:])
        finally:
            _backoff_wait_var.reset(wait_token)
        self.batch_succeeded = len(self.translated) > translated_before

    def _finish_batch(self, batch):
        a = self.args
        if self.streak.record(failed=bool(self.hard_failures), succeeded=self.batch_succeeded) \
                and not self.stop_run:
            self.stop_run = True
            self._add_error(batch, self.streak.stop_message(scrub_error_text(str(self.hard_failures[-1]))))
        if a.save_cb:
            a.save_cb(self.lines)
        if a.detail_cb:
            # No "done" message for a batch cut short by a cancel.
            if not (a.cancel_check_cb and a.cancel_check_cb()):
                self._report((self.bi + 1) / self.n_batches, f"{self.batch_label} done")
        elif a.progress_cb:
            a.progress_cb((self.bi + 1) / self.n_batches)

    def _cap_stops_run(self) -> bool:
        """A cost cap reached with batches still left ends the run cleanly."""
        a = self.args
        more = self.bi + 1 < self.n_batches
        if isinstance(self.engine, FallbackEngine) and self.engine.cap_exhausted() and more:
            if a.cap_cb:
                a.cap_cb(self.engine.spent[self.engine.active])
            return True
        if a.cost_cap_usd is not None and self.spent >= a.cost_cap_usd and more:
            if a.cap_cb:
                a.cap_cb(self.spent)
            return True
        return False

    def _recheck_pronouns(self):
        a = self.args
        if self.stop_run or (a.cancel_check_cb and a.cancel_check_cb()):
            return
        try:
            _recheck_male_pronouns(
                self.engine, self.lines, self.translated, self.context, self.character_names,
                self.size, a.save_cb, self._record_usage,
                lambda: a.cost_cap_usd is not None and self.spent >= a.cost_cap_usd,
                a.cancel_check_cb)
        except Exception as e:
            if not stops_translate_run(e):
                raise
            self.errors.append({"batch_index": self.n_batches - 1, "lines": [],
                                "error": redact_secrets(str(e))})


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
