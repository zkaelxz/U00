"""Reflect mode: three LLM passes (draft, critique, rewrite) over one batch."""

from .prompts import batch_context_for, build_stable_prompt, pronoun_batch_note
from .medium_prompts import kind_for
from .reflect_prompts import (build_reflect_expressive_task, build_reflect_faithful_task,
                              build_reflect_reflection_task)
from .shared import _id_keyed_batch_request, spoken_language_tag
from .llm_tasks import call_llm_json


REFLECT_PASSES = ("draft", "critique", "rewrite")


def reflect_translate_batch(engine, zh_lines: list, context: dict, usage_cb=None, max_retries: int = 1,
                            pass_cb=None, passes: int = len(REFLECT_PASSES), resume: dict | None = None):
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

    passes: 3 (default) runs all of the above; 1 stops after the faithfulness
    draft and returns it as the translation with no critiques. Internal: no
    setting selects it.

    resume: a dict the caller keeps across attempts at the same batch. A pass
    that finished is stored in it, so an attempt retried after a later pass
    failed starts from that pass instead of sending (and paying for) the
    finished ones again.

    The stable instructions go in a system block (cached on Claude), so the
    three passes and every later batch re-read them from the cache.
    usage_cb must therefore accept (input, output, cache_read, cache_write).
    """
    if passes not in (1, len(REFLECT_PASSES)):
        raise ValueError("Reflect runs 1 pass (draft only) or all 3")
    if not getattr(engine, "supports_reference", False):
        raise RuntimeError(f"{getattr(engine, 'name', type(engine).__name__)} can't run Reflect "
                           "mode (needs an LLM engine, not a translation-only one).")

    ids = _batch_ids(zh_lines, context)
    line_languages = context.get("line_languages")
    if line_languages and len(line_languages) == len(zh_lines):
        # Tagged here so all three passes show the model each source's language.
        zh_lines = [spoken_language_tag(lang) + zh for lang, zh in zip(line_languages, zh_lines)]
    sources = dict(zip(ids, zh_lines))
    speaker_names = context.get("speaker_labels")
    speakers = {i: speaker_names[p] for p, i in enumerate(ids)} if speaker_names else None
    instructions, kind, batch_ctx = _prompt_parts(context)

    def call(prompt):
        return call_llm_json(engine, prompt, max_tokens=4000, fallback="{}", usage_cb=usage_cb,
                             system=instructions)

    def build_faithful_batch(batch_ids):
        return build_reflect_faithful_task(
            batch_ctx, batch_ids, {i: sources[i] for i in batch_ids},
            {i: speakers[i] for i in batch_ids} if speakers else None, kind)

    def announce(pass_no):
        if pass_cb:
            pass_cb(pass_no, REFLECT_PASSES[pass_no - 1])

    done = {} if resume is None else resume
    if "draft" not in done:
        announce(1)
        direct_map = _id_keyed_batch_request(ids, build_faithful_batch, call, max_retries)
        done["draft"] = {i: direct_map.get(str(i), "") for i in ids}
    direct = done["draft"]
    if passes == 1:
        return [direct[i] for i in ids], ["" for _ in ids]
    critique_map, final_map = _critique_and_rewrite(ids, sources, direct, batch_ctx, kind, call,
                                                    max_retries, done, announce)
    translations = [final_map.get(str(i), direct.get(i, "")) for i in ids]
    critiques = [critique_map.get(str(i), "") for i in ids]
    return translations, critiques


def _batch_ids(zh_lines: list, context: dict) -> list:
    """The ids the passes key lines by: the caller's line ids when they are one distinct int per
    line, else positions from 1."""
    line_ids = context.get("line_ids")
    if (line_ids is not None and len(line_ids) == len(zh_lines)
            and all(isinstance(i, int) for i in line_ids) and len(set(line_ids)) == len(line_ids)):
        return list(line_ids)
    return list(range(1, len(zh_lines) + 1))


def _prompt_parts(context: dict) -> tuple:
    """(system instructions, medium kind, batch context) shared by the three passes."""
    # build_llm_instructions() alone never actually inserts the
    # reference novel text anywhere -- only build_stable_prompt()'s own
    # novel_block does that (the normal, non-Reflect path already goes
    # through it). Calling build_llm_instructions() directly here meant
    # Reflect mode's own instructions told the model to consult "the
    # reference novel translation below," but nothing was ever below it.
    instructions, novel_block = build_stable_prompt(context)
    if novel_block:
        instructions = instructions + "\n\n" + novel_block
    kind = kind_for(context.get("drama_meta") or {})
    batch_ctx = batch_context_for(context)
    batch_ctx = batch_ctx + "\n" if batch_ctx else ""
    batch_ctx = pronoun_batch_note(context.get("style_guidelines"),
                                   context.get("source_language", "zh")) + batch_ctx
    return instructions, kind, batch_ctx


def _critique_and_rewrite(ids, sources, direct, batch_ctx, kind, call, max_retries, done,
                          announce) -> tuple:
    """Passes 2 and 3. Returns ({id: critique}, {id: rewrite}) keyed by str(id)."""
    def build_reflection_batch(batch_ids):
        return build_reflect_reflection_task(
            batch_ctx, batch_ids, {i: sources[i] for i in batch_ids}, direct, kind)

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
    if "critique" not in done:
        if has_draft_ids:
            announce(2)
        done["critique"] = (_id_keyed_batch_request(has_draft_ids, build_reflection_batch, call,
                                                    max_retries=0) if has_draft_ids else {})
    critique_map = done["critique"]

    def build_expressive_batch(batch_ids):
        return build_reflect_expressive_task(
            batch_ctx, batch_ids, {i: sources[i] for i in batch_ids}, direct,
            {i: critique_map.get(str(i)) for i in batch_ids}, kind)

    if has_draft_ids:
        announce(3)
    final_map = (_id_keyed_batch_request(has_draft_ids, build_expressive_batch, call, max_retries)
                 if has_draft_ids else {})
    return critique_map, final_map
