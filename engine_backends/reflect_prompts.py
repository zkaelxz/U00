"""Prompts for Reflect mode's three passes. Each pass has a *_task builder (the
per-batch text) and a *_prompt builder (instructions + task as one string, for
bulk submission); the live path sends the instructions as a cached system block."""

from .shared import build_numbered_lines


def build_reflect_faithful_task(batch_ctx: str, ids: list, zh_by_id: dict,
                                speaker_by_id: dict = None) -> str:
    """Pass 1 (faithfulness) text for one batch: everything in the prompt
    except the stable instructions, which the live path sends as a cached
    system block."""
    numbered = build_numbered_lines(
        ids, [zh_by_id[i] for i in ids],
        [speaker_by_id.get(i) for i in ids] if speaker_by_id else None)
    return (
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


def build_reflect_faithful_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                  speaker_by_id: dict = None) -> str:
    """Reflect mode's pass 1 prompt as one string, for bulk_translate.py's
    Reflect pipeline (Batch APIs take no per-request cache setup here). The
    live path sends the same text split into system + user."""
    return (f"{instructions}\n\n"
            + build_reflect_faithful_task(batch_ctx, ids, zh_by_id, speaker_by_id))


def build_reflect_reflection_task(batch_ctx: str, ids: list, zh_by_id: dict,
                                  draft_by_id: dict) -> str:
    """Pass 2 (reflection/critique) text without the instructions -- see
    build_reflect_faithful_task. draft_by_id: pass 1's own saved output for
    each id (never empty here -- the caller only includes ids that got an
    actual faithfulness draft)."""
    pairs = "\n".join(f"{i}. {zh_by_id[i]} -> {draft_by_id[i]}" for i in ids)
    return (
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


def build_reflect_reflection_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict) -> str:
    """Pass 2 prompt as one string -- see build_reflect_faithful_prompt."""
    return (f"{instructions}\n\n"
            + build_reflect_reflection_task(batch_ctx, ids, zh_by_id, draft_by_id))


def build_reflect_expressive_task(batch_ctx: str, ids: list, zh_by_id: dict,
                                  draft_by_id: dict, critique_by_id: dict) -> str:
    """Pass 3 (expressiveness/rewrite) text without the instructions -- see
    build_reflect_faithful_task. critique_by_id: pass 2's
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


def build_reflect_expressive_prompt(instructions: str, batch_ctx: str, ids: list, zh_by_id: dict,
                                    draft_by_id: dict, critique_by_id: dict) -> str:
    """Pass 3 prompt as one string -- see build_reflect_faithful_prompt."""
    return (f"{instructions}\n\n"
            + build_reflect_expressive_task(batch_ctx, ids, zh_by_id, draft_by_id, critique_by_id))
