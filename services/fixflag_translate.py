"""
services/fixflag_translate.py -- the re-translation half of "fix flagged lines".

Flagged lines go to the engine in batches, matched back by explicit id. An LLM
engine is told why each line was flagged and shown its current English, so it
can fix that one problem (or say the English is already right); a
translation-only engine can't take instructions and just translates the source
again. Either way a reply identical to the current English changes nothing: the
flag stays, because nothing was fixed. The caller saves the lines (field-scoped,
skipping any the user edited meanwhile; see fixflag_transcribe.save_fixed).
"""
import db
import translate_engines
from services import fixflag_transcribe

# Small enough that one reply with a reason per line fits the 4000-token reply
# cap, and that the cost cap is checked often.
BATCH_SIZE = 15


def _reason_text(ln) -> str:
    reason = translate_engines.flag_reason_label(ln.flag)
    return f"{reason}: {ln.flag_note}" if (ln.flag_note or "").strip() else reason


def build_fix_prompt(batch: list, ids: dict, instructions: str, batch_context: str = "") -> str:
    """ids maps each line's idx to the number it carries in the prompt and the
    reply. The current English is shown so the model can keep what is right."""
    entries = "\n".join(
        f"[{ids[ln.idx]}] Source: {ln.zh}\n    Current English: {ln.en or '(none)'}\n"
        f"    Flagged because: {_reason_text(ln)}" for ln in batch)
    return (
        f"{instructions}\n\n{batch_context}"
        "Each numbered line below was flagged for a second look. Fix the problem named for "
        "that line and change nothing else about it. If its current English is already "
        "right, return it exactly as it is. A line flagged as too long or too fast to read "
        "needs a shorter English line with the same meaning.\n\n"
        'Return ONLY a JSON object mapping each number to the final English line, e.g. '
        '{"1": "...", "2": "..."}. Include EVERY number you were given, and no numbers you '
        "weren't. No preamble, no markdown fences.\n\n" + entries)


def _llm_batch(batch, engine, base_context, usage_cb) -> dict:
    ids = {ln.idx: n for n, ln in enumerate(batch, 1)}
    instructions, _novel = translate_engines.build_stable_prompt(base_context)

    def call_model(text):
        return translate_engines.call_llm_json(engine, text, max_tokens=4000, fallback="{}",
                                               usage_cb=usage_cb)

    by_idx = {ln.idx: ln for ln in batch}
    by_number = {n: idx for idx, n in ids.items()}

    def build(numbers):
        return build_fix_prompt([by_idx[by_number[n]] for n in numbers], ids, instructions)

    result = translate_engines._id_keyed_batch_request(list(by_number), build, call_model)
    return {by_number[int(k)]: v for k, v in result.items() if isinstance(v, str)}


def _translate_batch(batch, engine, base_context, character_names, source_language) -> dict:
    out = engine.translate_batch(
        [ln.zh for ln in batch],
        {**base_context, "speaker_labels": [character_names.get(ln.speaker) for ln in batch],
         "line_ids": [ln.idx for ln in batch],
         "line_languages": translate_engines.tagged_line_languages(batch, source_language)})
    return {ln.idx: text for ln, text in zip(batch, out)}


def fix_lines(flagged, *, engine, engine_choice, drama_id, base_context, character_names,
              source_language, heard_by_idx, translate, cost_cap_usd, check_cancel,
              on_progress, errors) -> dict:
    """Applies heard text, then re-translates what needs it. translate=False
    (a Cancel seen while hearing) saves the heard text and spends nothing.
    Per-line failures are appended to `errors` and leave that line flagged.
    Returns {"fixed", "unchanged", "cap_reached"}."""
    fixed = unchanged = 0
    spent = 0.0
    cap_reached = None
    to_translate = []
    for ln in flagged:
        heard = heard_by_idx.get(ln.idx)
        if heard is not None:
            if heard.get("error"):
                errors.append(heard["error"])
            elif heard["text"]:
                fixflag_transcribe.use_heard_text(ln, heard["text"])
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
                replies = _llm_batch(batch, engine, base_context, log_usage)
            else:
                replies = _translate_batch(batch, engine, base_context, character_names,
                                           source_language)
                if hasattr(engine, "last_usage"):
                    log_usage(engine.last_usage.get("input_tokens", 0),
                              engine.last_usage.get("output_tokens", 0))
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
