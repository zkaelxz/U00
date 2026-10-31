"""Batching a novel by paragraph for translation."""

import os

from . import medium_prompts


def plan_paragraph_batches(lines, max_size: int, ends, tail_fraction: float = 0.5) -> list:
    """Like plan_batches for a novel: a batch ends at a paragraph end instead
    of mid-paragraph. `ends` is the set of line idx that end a paragraph. A
    batch may end early only inside its last `tail_fraction` of lines, at the
    latest paragraph end there; with none it is cut at `max_size`, so a
    paragraph longer than a batch is still split rather than sent whole."""
    batches = []
    start, n = 0, len(lines)
    while start < n:
        end = min(start + max_size, n)
        if end < n:
            earliest = max(start + 1, end - int(max_size * tail_fraction))
            for cut in range(end, earliest - 1, -1):
                if lines[cut - 1].idx in ends:
                    end = cut
                    break
        batches.append(lines[start:end])
        start = end
    return batches


def novel_paragraph_ends(drama_meta, lines):
    """Paragraph ends of a novel title's saved source text, or None when the
    title has no such text or the lines no longer match it."""
    drama_id = (drama_meta or {}).get("id")
    if medium_prompts.kind_for(drama_meta) != "novel" or not drama_id:
        return None
    import db
    from novel_text import narration_paragraph_ends
    return narration_paragraph_ends(lines, os.path.join(db.DRAMAS_DIR, str(drama_id)))
