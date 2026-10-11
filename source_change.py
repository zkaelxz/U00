"""
source_change.py -- the `source_changed` review flag (pure apart from
save_cleared_flags). A line whose source text changes after it was
translated keeps its English and is flagged, so the
mismatch is reviewed instead of silently exported (or silently lost, as a
cleared translation would be). New English for the line, from a translate run
or the user, clears the flag; nothing else does.
"""
import dataclasses

SOURCE_CHANGED_FLAG = "source_changed"


def orphans_english(old_zh, new_zh, en) -> bool:
    """True when replacing old_zh with new_zh leaves `en` translating text
    the line no longer has. Whitespace-only edits don't count, and a line
    with no English (or no source before) has nothing to orphan."""
    old = (old_zh or "").strip()
    return bool((en or "").strip()) and bool(old) and old != (new_zh or "").strip()


def flag_values(old_zh, new_zh, en) -> dict:
    """The flag columns a compare-and-set writer adds next to its new `zh`:
    {} when the English isn't orphaned."""
    if not orphans_english(old_zh, new_zh, en):
        return {}
    return {"flag": SOURCE_CHANGED_FLAG, "flag_note": ""}


def set_source(ln, new_zh) -> bool:
    """Gives the Line new source text, flagging it when that orphans its
    English. True when it flagged the line."""
    values = flag_values(ln.zh, new_zh, ln.en)
    ln.zh = new_zh
    if values:
        ln.flag, ln.flag_note = values["flag"], values["flag_note"]
    return bool(values)


def clear_for_new_english(ln) -> bool:
    """New English answers a source change; any other flag is left alone.
    True when it cleared the flag."""
    if ln.flag != SOURCE_CHANGED_FLAG:
        return False
    ln.flag, ln.flag_note = None, ""
    return True


def save_cleared_flags(drama_id: int, lines) -> None:
    """Saves the `source_changed` flags a translate run's new English
    cleared on its own copies (clear_for_new_english). Each is
    a compare-and-set on the flag, the source the run translated and the
    English it wrote, so a line whose source changed again or whose English
    the user edited meanwhile isn't touched; that copy gets the flag back, so
    the run's later flag save can't clear it either."""
    # db imports core, which imports this module.
    import db

    cleared = [ln for ln in lines if getattr(ln, "id", None) is not None and ln.flag is None
               and (ln.orig or {}).get("flag") == SOURCE_CHANGED_FLAG]
    if not cleared:
        return
    copies = [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in cleared]
    kept = set(db.save_lines(drama_id, copies, fields=("flag", "flag_note"),
                             only_if_unchanged=True, guard_fields=("zh", "en")) or ())
    for ln in cleared:
        if ln.id in kept:
            ln.flag, ln.flag_note = ln.orig["flag"], ln.orig.get("flag_note") or ""
        else:
            ln.orig.update(flag=None, flag_note="")
