"""Translation notes on idioms, wordplay, allusions and meaningful names, and their grouping and Markdown export."""

from engine_backends.run_limits import TranslateRunStopped
from engine_backends.llm_tasks import call_llm_json
from engine_backends.shared import parse_json_array


# ---------------------------------------------------------------------------
# Translation notes -- idioms, wordplay, allusions, meaningful names
# ---------------------------------------------------------------------------

NOTE_TYPES = {
    "idiom": "成语 / set phrase whose imagery or origin is worth explaining",
    "wordplay": "Pun, homophone, or double meaning that can't survive translation",
    "name_meaning": "Name whose characters carry thematic meaning",
    "allusion": "Reference to poetry, classics, history, or another work",
    "cultural": "Custom, object, or social convention unfamiliar to English readers",
    "honorific": "Form of address whose nuance is lost in direct translation",
}


def build_translation_notes_prompt(batch: list, id_fn=lambda ln: ln.idx) -> str:
    """The translation-notes prompt for one batch, each line numbered by
    id_fn(ln) (position by default, matching generate_translation_notes_llm's
    own "line_idx" output field). bulk_translate.py's bulk submission
    passes id_fn=lambda ln: ln.id -- see
    engine_backends.llm_tasks.build_flag_prompt's docstring for why a permanent
    id matters once results can come back hours later."""
    types_desc = "\n".join(f"  - {k}: {v}" for k, v in NOTE_TYPES.items())
    pairs = "\n".join(f"[{id_fn(ln)}] {ln.zh} → {ln.en}" for ln in batch)
    return (
        "Below are source lines paired with their English translations from a baihe "
        "(GL) work. Identify places where something meaningful did NOT survive the "
        "translation and is worth a translation note for readers.\n\n"
        f"Note types:\n{types_desc}\n\n"
        "Be selective -- only flag things a reader would genuinely benefit from knowing. "
        "Do not flag ordinary translation choices, and do not flag the same term more "
        "than once. Write each note as one or two plain sentences a reader can absorb "
        "quickly; don't lecture.\n\n"
        'Return ONLY a JSON array: [{"line_idx": 0, "term": "the original term/phrase", '
        '"note_type": "...", "note": "..."}]. Empty array if nothing is worth noting. '
        "No preamble, no markdown fences.\n\n" + pairs
    )


def generate_translation_notes_llm(lines, engine, batch_size: int = 40, usage_cb=None,
                                   cancel_check=None):
    """
    Reviews translated lines for things that lost something in translation
    and are worth a translation note: idioms, puns, meaningful names,
    literary allusions, cultural specifics, honorific nuance.

    Returns a list of {line_idx, term, note_type, note} -- for review and
    optional export as a notes appendix. Doesn't modify any line text.
    cancel_check: called before each batch; it may raise to stop the
    run between batches (a batch already sent still finishes).
    """
    if not getattr(engine, "supports_reference", False):
        return []

    translated = [ln for ln in lines if ln.en.strip()]
    if not translated:
        return []

    all_notes = []

    for start in range(0, len(translated), batch_size):
        if cancel_check:
            cancel_check()
        batch = translated[start:start + batch_size]
        prompt = build_translation_notes_prompt(batch)
        try:
            text = call_llm_json(engine, prompt, max_tokens=3000, fallback="[]", usage_cb=usage_cb,
                                 expect="json_array")
        except TranslateRunStopped:
            break  # the notes found so far are still returned
        notes = parse_json_array(text, 0)
        if isinstance(notes, list):
            for n in notes:
                if isinstance(n, dict) and n.get("note"):
                    n["note_type"] = n.get("note_type") if n.get("note_type") in NOTE_TYPES else "cultural"
                    all_notes.append(n)
    return all_notes


def group_notes_by_line(notes) -> dict:
    """notes: rows from db.list_translation_notes() (each has line_idx,
    term, note). Returns {line_idx: [{"term", "note"}, ...]} -- the shape
    core.lines_to_srt()/lines_to_bilingual_srt() take to inline notes
    into exported subtitles, right on the line each one applies to.
    Notes with no line_idx (not tied to a specific line) are dropped --
    inlining them into the subtitle track has nowhere sensible to go;
    they're still in the Markdown appendix and the in-app Reader.
    The reflection notes (note_type "reflection" -- see
    engine_backends.translate_pipeline.translate_lines_with_engine's notes_cb) are dropped
    too --
    a translator's own reasoning about a line's wording is for review,
    not a reader-facing aside, and it has no `term` to introduce it
    (unlike an idiom/allusion/etc. note, which names what it's about).
    """
    grouped = {}
    for n in notes:
        idx = n.get("line_idx")
        if idx is None or n.get("note_type") == "reflection":
            continue
        grouped.setdefault(idx, []).append({"term": n.get("term", ""), "note": n.get("note", "")})
    return grouped


def format_notes_as_markdown(notes, drama_title: str = "") -> str:
    """Renders translation notes as a readable appendix -- for export
    alongside subtitles, or as an afterword in an EPUB."""
    if not notes:
        return "# Translation Notes\n\n(No notes recorded.)\n"

    header = f"# Translation Notes{f' — {drama_title}' if drama_title else ''}\n\n"
    by_type: dict[str, list] = {}
    for n in notes:
        by_type.setdefault(n.get("note_type", "cultural"), []).append(n)

    sections = []
    for note_type, items in by_type.items():
        label = NOTE_TYPES.get(note_type, note_type).split("/")[0].strip()
        lines = [f"## {label.title()}\n"]
        for n in sorted(items, key=lambda x: x.get("line_idx", 0)):
            line_ref = f"Line {n['line_idx'] + 1}" if n.get("line_idx") is not None else ""
            term = f"**{n['term']}**" if n.get("term") else ""
            lines.append(f"- {line_ref} — {term}: {n['note']}")
        sections.append("\n".join(lines))
    return header + "\n\n".join(sections) + "\n"
