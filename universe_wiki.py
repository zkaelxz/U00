"""
universe_wiki.py -- builds an encyclopedia of the story's world as you
read: characters, places, sects, artifacts, concepts, and events.

The defining constraint is that it's SPOILER-BOUNDED. Entries are
extracted only from lines up to a given point, and each entry records
how far into the story it was built from. An entry introduced at line
500 will not surface for someone who has read to line 100 -- so asking
"who is this again?" never accidentally reveals a betrayal, a death,
or a hidden identity you haven't reached.

Entries accumulate: re-running extraction after reading further updates
existing entries with new information rather than starting over.
"""

import re
import json
from translate_engines import call_llm_json

ENTRY_TYPES = {
    "character": "A person in the story",
    "place": "City, palace, mountain, region, or building",
    "sect": "Sect, clan, school, organization, or faction",
    "artifact": "Weapon, treasure, pill, or notable object",
    "concept": "Cultivation system, technique, rule, or worldbuilding concept",
    "event": "A significant event that has occurred",
}


def extract_wiki_entries(lines, engine, up_to_line_idx: int, drama_meta: dict = None,
                          existing_entries=None, chunk_size: int = 150):
    """
    Reads lines[0 : up_to_line_idx+1] and extracts encyclopedia entries.

    existing_entries: current wiki, so the model updates and extends
    rather than duplicating -- this is what makes the wiki accumulate
    coherently across multiple runs as you read further.

    Returns a list of entry dicts ready for db.upsert_wiki_entry().
    """
    if not getattr(engine, "supports_reference", False):
        return []

    in_scope = [ln for ln in lines if ln.idx <= up_to_line_idx and (ln.en or ln.zh)]
    if not in_scope:
        return []

    known_block = ""
    if existing_entries:
        summary = "; ".join(
            f"{e['name']} ({e['entry_type']})" for e in existing_entries[:80])
        known_block = (
            f"\n\nAlready in the wiki -- UPDATE these with new information rather than "
            f"re-describing from scratch, and add genuinely new entries: {summary}")

    types_desc = "\n".join(f"  - {k}: {v}" for k, v in ENTRY_TYPES.items())
    title = (drama_meta or {}).get("title_en") or (drama_meta or {}).get("title_zh") or "this work"

    all_entries = {}
    for start in range(0, len(in_scope), chunk_size):
        chunk = in_scope[start:start + chunk_size]
        excerpt = "\n".join(
            f"[{ln.idx}] ({ln.speaker or '?'}) {ln.zh} → {ln.en}" for ln in chunk)

        prompt = (
            f"From this excerpt of \"{title}\", extract encyclopedia entries.\n\n"
            f"Entry types:\n{types_desc}\n\n"
            "For each entry give: type, name (as most commonly used), any aliases/titles/"
            "nicknames it's also called, a concise description, and relevant attributes "
            "as key-value pairs (e.g. cultivation level, rank, sect affiliation, notable "
            "equipment, relationships). Also give first_seen_line_idx -- the line number "
            "in brackets where it first appears in THIS excerpt.\n\n"
            "Base entries ONLY on what this excerpt actually establishes. Do not draw on "
            "outside knowledge of similar stories, and do not speculate about what a "
            "character might later turn out to be.\n\n"
            'Return ONLY a JSON array: [{"entry_type": "...", "name": "...", "aliases": '
            '"comma, separated", "description": "...", "attributes": {"key": "value"}, '
            '"first_seen_line_idx": 0}]. No preamble, no markdown fences.'
            + known_block + f"\n\nExcerpt:\n{excerpt}"
        )

        text = call_llm_json(engine, prompt, max_tokens=4000, fallback="[]")
        text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            entries = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(entries, list):
            continue

        for e in entries:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            etype = e.get("entry_type") if e.get("entry_type") in ENTRY_TYPES else "concept"
            key = (etype, e["name"])
            e["entry_type"] = etype
            e["known_through_line_idx"] = up_to_line_idx
            if key in all_entries:
                # merge: later chunks refine earlier ones
                prev = all_entries[key]
                if e.get("description"):
                    prev["description"] = e["description"]
                if e.get("attributes"):
                    prev.setdefault("attributes", {}).update(e["attributes"])
                if e.get("aliases"):
                    merged = set(filter(None,
                                        (prev.get("aliases", "") + "," + e["aliases"]).split(",")))
                    prev["aliases"] = ", ".join(sorted(a.strip() for a in merged if a.strip()))
                prev["known_through_line_idx"] = up_to_line_idx
            else:
                all_entries[key] = e
    return list(all_entries.values())


def format_wiki_as_markdown(entries, drama_title: str = "", spoiler_note: str = "") -> str:
    """Renders the wiki as a readable document, grouped by entry type."""
    if not entries:
        return "# Universe Wiki\n\n(No entries yet.)\n"
    header = f"# Universe Wiki{f' — {drama_title}' if drama_title else ''}\n\n"
    if spoiler_note:
        header += f"_{spoiler_note}_\n\n"

    by_type = {}
    for e in entries:
        by_type.setdefault(e.get("entry_type", "concept"), []).append(e)

    sections = []
    for etype in ENTRY_TYPES:
        items = by_type.get(etype)
        if not items:
            continue
        lines = [f"## {etype.title()}s\n"]
        for e in sorted(items, key=lambda x: x.get("name", "")):
            lines.append(f"### {e.get('name')}")
            if e.get("aliases"):
                lines.append(f"*Also known as: {e['aliases']}*")
            if e.get("description"):
                lines.append(f"\n{e['description']}")
            attrs = e.get("attributes") or {}
            if attrs:
                lines.append("")
                for k, v in attrs.items():
                    lines.append(f"- **{k}**: {v}")
            lines.append("")
        sections.append("\n".join(lines))
    return header + "\n".join(sections)


