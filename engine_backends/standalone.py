"""Standalone (non-drama) text translation."""

import re
from core import LANGUAGE_NAMES


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
                         batch_size: int = 8) -> str:
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
    """
    ok, message = standalone_direction_support(engine.name, source_language, target_language)
    if not ok:
        raise UnsupportedDirectionError(message)
    chunks = chunk_standalone_text(text)
    if not chunks:
        return ""
    context = {
        "source_language": source_language,
        "target_language": target_language,
        "standalone": True,
    }
    translated_chunks = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        translated_chunks.extend(engine.translate_batch(batch, context))
    return "\n\n".join(t or "" for t in translated_chunks)
