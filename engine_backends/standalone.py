"""Standalone (non-drama) text translation."""

import re
from core import LANGUAGE_NAMES
from .llm_tasks import call_batch_bounded
from .shared import LLMTaskTimeout


class UnsupportedDirectionError(Exception):
    """Raised by standalone_translate when the requested engine
    can't handle the requested translation direction -- see
    standalone_direction_support for which engines/directions this
    applies to and why."""


def standalone_direction_support(engine_name: str, source_language: str, target_language: str):
    """(ok, message) for whether engine_name can translate FROM
    source_language TO target_language in the standalone translate
    tool. ok=False means refuse outright -- the caller must not call
    translate_batch at all. ok=True with a message means attempt it but
    show the message as a warning; ok=True with message=None means no
    caveat.

    zh/ja/ko -> English is this app's existing, well-tested direction --
    every engine already does this and keeps doing it unchanged. English
    -> zh/ja/ko is new:
      - NLLB takes an explicit source+target pair in its own
        pipeline, so they're just as capable in either direction.
      - Claude/DeepSeek/Gemini are prompted for the direction
        directly (build_standalone_instructions), same as any other LLM
        instruction.
      - Ollama's real capability depends entirely on whichever local model
        is loaded, which this app has no way to verify -- attempted, but
        flagged as a warning rather than assumed reliable.
    """
    if source_language != "en":
        return True, None
    if engine_name == "ollama":
        target_name = LANGUAGE_NAMES.get(target_language, target_language)
        return True, (
            f"Ollama's quality translating English -> {target_name} depends entirely on "
            "which local model you have loaded -- some handle it well, some not at all. "
            "Check the output carefully.")
    return True, None


def chunk_standalone_text(text: str, max_chars_per_chunk: int = 1500) -> list:
    """Splits text into paragraph-based chunks for the standalone
    translate tool, keeping paragraph breaks intact so translated chunks
    can be rejoined the same way. Consecutive short paragraphs are grouped
    up to max_chars_per_chunk; a single paragraph longer than that is kept
    whole rather than cut mid-sentence."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return []
    chunks = []
    current = []
    current_len = 0
    for p in paragraphs:
        if current and current_len + len(p) > max_chars_per_chunk:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(p)
        current_len += len(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def standalone_translate(text: str, engine, source_language: str, target_language: str,
                         batch_size: int = 8, budget=None) -> str:
    """The standalone translate tool: translates arbitrary pasted
    text -- not tied to any drama/project -- from source_language to
    target_language (one side of which is always "en"). Chunks long text
    with chunk_standalone_text, sent in groups of batch_size per
    translate_batch call (same reasoning as translate_lines_with_engine's
    own batch_size, just simpler since there's no cross-batch context
    window here), and reassembled using each engine's own translate_batch
    -- which already resolves a response back to its own chunk by id
    rather than by position (see request_translations_with_retry's own
    docstring for the exact bug that protects against) -- built once
    there, not reimplemented here.

    Raises UnsupportedDirectionError if standalone_direction_support
    refuses this engine/direction combination; callers should check that
    first (to show a live warning/refusal in the UI) but this checks
    again itself so it's never silently skipped by a caller that forgets.

    budget: optional object with refuse(batch, first_chunk, total_chunks),
    called before each request and returning a plain message to stop the run
    there (the text finished so far is still returned, see
    standalone_translate_partial), and abandoned(batch), called when a request
    passed its deadline and was left running.
    """
    return standalone_translate_partial(text, engine, source_language, target_language,
                                        batch_size, budget)[0]


def _line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _paragraph_lines(chunk: str) -> list:
    return [[line.strip() for line in p.splitlines() if line.strip()]
            for p in re.split(r"\n\s*\n", chunk.strip()) if p.strip()]


def standalone_requests(text: str, batch_size: int = 8) -> list:
    """The requests (lists of items) standalone_translate_partial sends on a
    first pass, so a price estimate counts the same requests the run makes.
    A redo of a blob chunk whose reply came back with the wrong line count is
    not counted."""
    requests = []
    chunks = chunk_standalone_text(text)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        blob = [c for c in batch if all(len(p) == 1 for p in _paragraph_lines(c))]
        if blob:
            requests.append(blob)
        for chunk in batch:
            if chunk in blob:
                continue
            lines = [ln for p in _paragraph_lines(chunk) for ln in p if re.search(r"\w", ln)]
            requests.extend(lines[i:i + batch_size] for i in range(0, len(lines), batch_size))
    return requests


def standalone_translate_partial(text: str, engine, source_language: str, target_language: str,
                                 batch_size: int = 8, budget=None) -> tuple:
    """(translated text, message or None): standalone_translate, plus why the
    run ended early when the budget stopped it, or how many lines could not be
    translated.

    A chunk with single-newline lines inside a paragraph is sent one line per
    item: sent as one blob, a model merges lines or reads the text's own
    "1." numbers as its numbering, and the output stops lining up with the
    input. A blob chunk whose reply has a different line count is redone line
    by line for the same reason. A line the engine still does not answer is
    kept in place as "[untranslated] <source line>", never merged."""
    ok, message = standalone_direction_support(engine.name, source_language, target_language)
    if not ok:
        raise UnsupportedDirectionError(message)
    chunks = chunk_standalone_text(text)
    if not chunks:
        return "", None
    context = {
        "source_language": source_language,
        "target_language": target_language,
        "standalone": True,
    }
    stopped = None
    missing = 0

    def send(batch, position):
        nonlocal stopped
        if budget is not None:
            stopped = budget.refuse(batch, position, len(chunks))
            if stopped:
                return None
        try:
            reply = call_batch_bounded(engine, lambda: engine.translate_batch(batch, context))
        except LLMTaskTimeout:
            # The request may still be running and billed; nothing else will log it.
            if budget is not None:
                budget.abandoned(batch)
            raise
        # A reply of the wrong length can't be matched to its items by position.
        return reply if len(reply) == len(batch) else [None] * len(batch)

    def translate_by_line(chunk, position):
        """The chunk rebuilt from per-line translations, or None when the
        budget stopped the run."""
        nonlocal missing
        paragraphs = _paragraph_lines(chunk)
        lines = [line for p in paragraphs for line in p]
        # No letters or digits: nothing to translate, and an engine may answer "".
        sendable = [i for i, line in enumerate(lines) if re.search(r"\w", line)]
        out = {i: line for i, line in enumerate(lines) if i not in sendable}
        for start in range(0, len(sendable), batch_size):
            ids = sendable[start:start + batch_size]
            reply = send([lines[i] for i in ids], position)
            if reply is None:
                return None
            for i, value in zip(ids, reply):
                if value and value.strip():
                    out[i] = value
                else:
                    out[i] = f"[untranslated] {lines[i]}"
                    missing += 1
        rebuilt, n = [], 0
        for p in paragraphs:
            rebuilt.append("\n".join(out[n + k] for k in range(len(p))))
            n += len(p)
        return "\n\n".join(rebuilt)

    translated_chunks = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        results = {}
        blob = [k for k, c in enumerate(batch) if all(len(p) == 1 for p in _paragraph_lines(c))]
        if blob:
            reply = send([batch[k] for k in blob], start)
            if reply is None:
                break
            for k, value in zip(blob, reply):
                if value is not None and _line_count(value) == _line_count(batch[k]):
                    results[k] = value
        for k, chunk in enumerate(batch):
            if k not in results:
                results[k] = translate_by_line(chunk, start)
                if results[k] is None:
                    break
        if stopped:
            break
        translated_chunks.extend(results[k] for k in range(len(batch)))
    if missing and not stopped:
        stopped = (f"{missing} lines could not be translated and are marked [untranslated]."
                   if missing != 1 else "1 line could not be translated and is marked [untranslated].")
    return "\n\n".join(t or "" for t in translated_chunks), stopped
