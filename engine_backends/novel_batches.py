"""Batching a novel by paragraph for translation."""

import os
from collections.abc import Collection

from . import medium_prompts


def plan_paragraph_batches(lines, max_size: int, ends, tail_fraction: float = 0.5,
                           max_chars: int | None = None,
                           chapter_starts: Collection[int] = frozenset()) -> list:
    """Like plan_batches for a novel: a batch ends at a chapter or paragraph end
    instead of mid-paragraph. `ends` is the set of line idx that end a paragraph;
    `chapter_starts` the idx of lines that open a chapter. A batch holds at most
    `max_size` lines and, when given, about `max_chars` source characters (the
    line that crosses it ends the batch). It may end early only inside its last
    `tail_fraction`, at the latest chapter start there, else the latest paragraph
    end; with neither it is cut at the limit, so a paragraph longer than a batch
    is still split rather than sent whole. A chapter start is preferred so the
    parts of one chapter stay in the same requests and a request does not run into
    the next chapter."""
    batches = []
    start, n = 0, len(lines)
    while start < n:
        end = min(start + max_size, n)
        if max_chars:
            total = 0
            for i in range(start, end):
                total += len(getattr(lines[i], "zh", "") or "")
                if total > max_chars and i > start:
                    end = i
                    break
        if end < n:
            earliest = max(start + 1, end - int((end - start) * tail_fraction))
            cut = next((c for c in range(end, earliest - 1, -1)
                        if lines[c].idx in chapter_starts), None)
            if cut is None:
                cut = next((c for c in range(end, earliest - 1, -1)
                            if lines[c - 1].idx in ends), None)
            if cut is not None:
                end = cut
        batches.append(lines[start:end])
        start = end
    return batches


def chapter_start_idxs(lines) -> set:
    """idx of the lines that open a chapter. Headings in a row count as one start, chosen as
    novel_text.chapter_starts chooses it: the first of a pair (a title printed twice, or a volume
    heading over a chapter heading), the last of a longer run (a contents page)."""
    from novel_text import _HEADING_RUN, is_chapter_heading
    starts, run = set(), []

    def close_run():
        if run:
            starts.add((run[0] if len(run) <= _HEADING_RUN else run[-1]).idx)
            run.clear()

    for ln in lines:
        if is_chapter_heading(ln):
            run.append(ln)
        else:
            close_run()
    close_run()
    return starts


def novel_paragraph_ends(drama_meta, lines):
    """Paragraph ends of a novel title's saved source text, or None when the
    title has no such text or the lines no longer match it."""
    drama_id = (drama_meta or {}).get("id")
    if medium_prompts.kind_for(drama_meta) != "novel" or not drama_id:
        return None
    import db
    from novel_text import narration_paragraph_ends
    return narration_paragraph_ends(lines, os.path.join(db.DRAMAS_DIR, str(drama_id)))
