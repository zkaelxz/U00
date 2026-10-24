"""
services/fixflag_translate.py -- the re-translation half of "fix flagged lines".

Flagged lines go to the engine in batches, matched back by explicit id. An LLM
engine is told why each line was flagged and shown its current English, so it
can fix that one problem (or say the English is already right); a
translation-only engine can't take instructions and just translates the source
again. Either way a reply identical to the current English changes nothing: the
flag stays, because nothing was fixed. The caller saves the lines (field-scoped,
skipping any the user edited meanwhile; see fixflag_transcribe.save_fixed).

Timing flags never reach the engine: rewording the English can't move a
subtitle onto its audio, so split_timing_flags drops them before any call.
"""
import db
import translate_engines
from engine_backends import llm_tasks
from engine_backends.run_limits import TranslateRunStopped
from services import fixflag_transcribe

# Small enough that one reply with a reason per line fits the 4000-token reply
# cap, and that the cost cap is checked often.
BATCH_SIZE = 15

# Neighbours shown on each side of a flagged line. Read-only: they let the model
# resolve pronouns and tone, and are never returned or saved.
CONTEXT_RADIUS = 3

# Flags a reworded English line can't fix. reading_speed is not one: a shorter
# English line is the fix for it.
TIMING_FLAGS = frozenset({"timing_uncertain", "timing_overlap", "timing_drift"})


def split_timing_flags(flagged: list):
    """(fixable, timing_skipped_count)."""
    fixable = [ln for ln in flagged if ln.flag not in TIMING_FLAGS]
    return fixable, len(flagged) - len(fixable)


def _reason_text(ln) -> str:
    reason = translate_engines.flag_reason_label(ln.flag)
    return f"{reason}: {ln.flag_note}" if (ln.flag_note or "").strip() else reason


def _context_entry(ln) -> str:
    return (f"(context only, do not return) Source: {ln.zh}\n"
            f"    Current English: {ln.en or '(none)'}\n")


def _entries(batch: list, ids: dict, context_lines: list, reasons: dict = None) -> str:
    """Flagged and context lines in script order. Windows around neighbouring
    flagged lines overlap, so each context line appears once; a gap between
    windows is marked."""
    position = {ln.idx: n for n, ln in enumerate(context_lines)}
    wanted = {}
    for ln in batch:
        at = position[ln.idx]
        for n in range(max(0, at - CONTEXT_RADIUS), min(len(context_lines), at + CONTEXT_RADIUS + 1)):
            wanted.setdefault(n, context_lines[n])
    flagged = {ln.idx for ln in batch}
    out, previous = [], None
    for n in sorted(wanted):
        ln = wanted[n]
        if previous is not None and n != previous + 1:
            out.append("...\n")
        previous = n
        out.append(
            f"[{ids[ln.idx]}] Source: {ln.zh}\n    Current English: {ln.en or '(none)'}\n"
            f"    Flagged because: {(reasons or {}).get(ln.idx) or _reason_text(ln)}\n"
            if ln.idx in flagged
            else _context_entry(ln))
    return "".join(out)


def build_fix_prompt(batch: list, ids: dict, instructions: str, batch_context: str = "",
                     context_lines: list = None, reasons: dict = None) -> str:
    """ids maps each line's idx to the number it carries in the prompt and the
    reply. The current English is shown so the model can keep what is right.
    context_lines is the whole script in order: up to CONTEXT_RADIUS lines
    either side of each flagged line are shown as read-only context. reasons
    maps idx to the reason shown, when it differs from the line's flag now."""
    entries = _entries(batch, ids, context_lines or batch, reasons)
    return (
        f"{instructions}\n\n{batch_context}"
        "Each numbered line below was flagged for a second look. Fix the problem named for "
        "that line and change nothing else about it. If its current English is already "
        "right, return it exactly as it is. A line flagged as too long or too fast to read "
        "needs a shorter English line with the same meaning. Lines marked \"context only, do "
        "not return\" are the surrounding script: read them to resolve who is speaking and "
        "what is meant, but never return them.\n\n"
        'Return ONLY a JSON object mapping each number to the final English line, e.g. '
        '{"1": "...", "2": "..."}. Include EVERY number you were given, and no numbers you '
        "weren't. No preamble, no markdown fences.\n\n" + entries)


def _llm_batch(batch, engine, base_context, usage_cb, context_lines, reasons=None) -> dict:
    ids = {ln.idx: n for n, ln in enumerate(batch, 1)}
    instructions, _novel = translate_engines.build_stable_prompt(base_context)

    def call_model(text):
        return translate_engines.call_llm_json(engine, text, max_tokens=4000, fallback="{}",
                                               usage_cb=usage_cb)

    by_idx = {ln.idx: ln for ln in batch}
    by_number = {n: idx for idx, n in ids.items()}

    def build(numbers):
        return build_fix_prompt([by_idx[by_number[n]] for n in numbers], ids, instructions,
                                context_lines=context_lines, reasons=reasons)

    result = translate_engines._id_keyed_batch_request(list(by_number), build, call_model)
    return {by_number[int(k)]: v for k, v in result.items() if isinstance(v, str)}


def _translate_batch(batch, engine, base_context, character_names, source_language) -> dict:
    def send():
        return engine.translate_batch(
            [ln.zh for ln in batch],
            {**base_context, "speaker_labels": [character_names.get(ln.speaker) for ln in batch],
             "line_ids": [ln.idx for ln in batch],
             "line_languages": translate_engines.tagged_line_languages(batch, source_language)})

    out = llm_tasks.call_batch_bounded(engine, send)
    # An engine API that only returns a list can't be matched by id, so a short
    # or long reply is a failed batch: pairing by position would put text on the
    # wrong line.
    if len(out) != len(batch):
        raise ValueError(f"the engine returned {len(out)} lines for {len(batch)} sent")
    return {ln.idx: text for ln, text in zip(batch, out)}


def fix_lines(flagged, *, engine, engine_choice, drama_id, base_context, character_names,
              source_language, heard_by_idx, translate, cost_cap_usd, check_cancel,
              on_progress, errors, all_lines=None) -> dict:
    """Applies heard text, then re-translates what needs it. translate=False
    (a Cancel seen while hearing) saves the heard text and spends nothing.
    Per-line failures are appended to `errors` and leave that line flagged.
    all_lines is the whole script, for read-only context in LLM prompts.
    Returns {"fixed", "unchanged", "cap_reached"}."""
    fixed = unchanged = 0
    spent = 0.0
    cap_reached = None
    to_translate = []
    reasons = {}
    for ln in flagged:
        heard = heard_by_idx.get(ln.idx)
        if heard is not None:
            if heard.get("error"):
                errors.append(heard["error"])
            elif heard["text"]:
                # New source text re-flags the line source_changed; the user's own
                # reason must still reach the model, and stay on the line if no
                # new English lands.
                reason, own_flag = _reason_text(ln), ln.flag
                if fixflag_transcribe.use_heard_text(ln, heard["text"]) and own_flag \
                        and own_flag != ln.flag:
                    reasons[ln.idx] = reason
                    ln.flag_note = reason
        if ln.zh.strip() and translate_engines.is_english_line(ln):
            ln.en = ln.zh
            ln.flag, ln.flag_note = None, ""
            fixed += 1
        elif ln.zh.strip() and translate:
            to_translate.append(ln)

    def log_usage(inp, out):
        nonlocal spent
        cost = translate_engines.estimate_cost_for_engine(engine, inp, out)
        spent += cost
        db.log_usage(drama_id, engine_choice, getattr(engine, "model", engine_choice),
                     "fix_flagged_line", inp, out, cost)

    llm = getattr(engine, "supports_reference", False)
    batches = [to_translate[i:i + BATCH_SIZE] for i in range(0, len(to_translate), BATCH_SIZE)]
    for n, batch in enumerate(batches):
        check_cancel()
        try:
            if llm:
                replies = _llm_batch(batch, engine, base_context, log_usage, all_lines or [],
                                     reasons)
            else:
                replies = _translate_batch(batch, engine, base_context, character_names,
                                           source_language)
                if hasattr(engine, "last_usage"):
                    log_usage(engine.last_usage.get("input_tokens", 0),
                              engine.last_usage.get("output_tokens", 0))
        except TranslateRunStopped:
            break  # the job's result names the reason; the lines stay flagged
        except Exception as e:
            for ln in batch:
                errors.append(translate_engines.redact_secrets(
                    f"line {ln.idx + 1} translation: {e}"))
            replies = {}
        for ln in batch:
            new = (replies.get(ln.idx) or "").strip()
            if not new:
                continue
            if new == (ln.en or "").strip():
                unchanged += 1
                continue
            ln.en = new
            ln.flag, ln.flag_note = None, ""
            fixed += 1
        on_progress(sum(len(b) for b in batches[:n + 1]), len(to_translate))
        if cost_cap_usd is not None and spent >= cost_cap_usd and n + 1 < len(batches):
            cap_reached = spent
            break
    return {"fixed": fixed, "unchanged": unchanged, "cap_reached": cap_reached}
