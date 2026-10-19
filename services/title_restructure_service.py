"""
services/title_restructure_service.py -- merging one title into another and
moving chapters between titles, for comics (pages, their image files and
chapter labels) and novels (chapter blocks of the raw-novel file).

Rules (owner defaults):
- Nothing is deleted: a merge moves every chapter and leaves the old title
  empty, for the owner to delete from the Library.
- Every change is previewed first (`preview`, which writes nothing) and
  applied only with `confirm` and the preview's `fingerprint`; anything that
  changed in between is refused (409).
- Refused up front while a job runs on either title, between different
  kinds of title or original languages, and for a merge whose old title holds
  something chapters can't carry (lines, audio, translation text).
- A snapshot of the database, plus the label and raw-novel files about to be
  rewritten, is taken before any write (newest KEEP_SNAPSHOTS kept, under
  backups/ so a full backup skips them).

Failure halfway never loses a chapter. Comics: page files are linked or
copied into the target first, the rows switch in one transaction, and the
originals go last; a failure before the commit removes the copies, one
after it leaves spare originals. Novels: the target file is written first,
then the source; if the source can't be written, the target (and anything
else already swapped) is put back from the snapshot.

Writers hold both titles' page locks (sources.pipeline, the lock page and
chapter imports use), lower id first, and register a restructure_<id> job on
both titles for the whole move, so job launches and deleting either title are
refused until it ends.

No FastAPI import. Responses carry ids, names, counts and the snapshot's
folder name, never a path or stored filename.
"""
import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time

import background_jobs
import comic_chapters
import db
from dub_narration import NOVEL_SOURCE_FILENAME
from services import drama_service, novel_chapters_service, ownership_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, ServiceError
from jobs import job_launch
from sources import chapter_manifest, pipeline, store

log = logging.getLogger(__name__)

KINDS = ("merge", "move")
COMIC_MEDIA_TYPES = ("manga", "manhwa", "manhua")
SNAPSHOT_SUBDIR = ("backups", "restructure")
KEEP_SNAPSHOTS = 10
MAX_CHAPTER_IDS = 5000
MAX_TARGETS = 500
_CHUNK = 1024 * 1024

_LEFTOVER_ORIGINALS = ("Some of the old title's original page files could not be removed (a "
                       "file may be in use). The pages are in the new title; the leftovers are "
                       "spare copies.")
_LABELS_NOT_MOVED = ("The pages moved, but their chapter names could not be updated, so they "
                     "show as \"Chapter unknown\".")
_TRACKING_NOT_MOVED = ("The chapters moved, but the source import records still name the old "
                       "title, so a later import may offer them again.")
_NOVEL_TRACKING = ("Novel chapters don't record which source chapter they came from, so this "
                   "title's source import records stay with it; a later import into the other "
                   "title may offer these chapters again.")
_TITLE_FIELDS_NOT_MERGED = ("The chapters moved, but the other title's tags, link or the "
                            "Merged tag could not be updated. Set them in Edit details.")
_HOLD_WAIT_SECONDS = 5.0


def _media(drama: dict):
    media = (drama.get("media_type") or "").lower()
    if media in COMIC_MEDIA_TYPES:
        return "comic"
    return "novel" if media == "novel" else None


def _name(drama: dict) -> str:
    return (drama.get("title_en") or drama.get("title_zh") or "").strip() or f"Title {drama['id']}"


def _require(drama_id, principal) -> dict:
    if isinstance(drama_id, bool) or not isinstance(drama_id, int):
        raise InvalidInputError("A title id must be a whole number.")
    drama = ownership_service.require_drama(drama_id)
    ownership_service.require_editable(principal, "drama", drama_id)
    return drama


@contextlib.contextmanager
def _page_locks(*drama_ids):
    with contextlib.ExitStack() as stack:
        for d in sorted(set(drama_ids)):
            stack.enter_context(pipeline._page_lock(d))
        yield


# ---------------------------------------------------------------------------
# Chapters of one title
# ---------------------------------------------------------------------------

def _comic_chapters(drama_id: int) -> dict:
    manifest, readable = comic_chapters.load_checked(drama_id)
    pages = db.list_pages(drama_id)
    groups = comic_chapters.group_pages(pages, manifest)
    by_file = {p["filename"]: p for p in pages}
    keys = {f: c["key"] for c in manifest["chapters"] for f in c["files"]}
    rows = []
    for g in groups:
        rows.append({"id": g["id"], "title": g["title"] or ("Chapter unknown" if not g["known"]
                                                            else g["chapter_id"] or "Chapter"),
                     "pages": g["page_count"], "chars": None, "in_translation": False,
                     "key": keys.get(g["filenames"][0]) if g["known"] else None,
                     "page_rows": [by_file[f] for f in g["filenames"]]})
    return {"readable": readable, "manifest": manifest, "pages": pages, "chapters": rows}


def _novel_chapters(drama_id: int) -> dict:
    found = chapter_manifest.safe_file(drama_id, chapter_manifest.RAW_NOVEL_FILENAME)
    present = os.path.lexists(chapter_manifest.raw_path(drama_id))
    out = {"readable": found is not None or not present, "size": 0, "mtime": 0,
           "split": False, "chapters": []}
    if found is None:
        return out
    out["size"], out["mtime"] = found[1].st_size, found[1].st_mtime_ns
    blocks, out["split"] = novel_chapters_service.file_blocks(drama_id, out["size"])
    listed = []
    while len(listed) < len(blocks):
        page = novel_chapters_service.list_chapters(drama_id, offset=len(listed),
                                                    limit=novel_chapters_service.MAX_PAGE)
        if not page["chapters"]:
            break
        listed.extend(page["chapters"])
    for n, b in enumerate(blocks, 1):
        row = listed[n - 1] if n <= len(listed) else {}
        out["chapters"].append({
            "id": str(n), "title": b["title"] or f"Chapter {n}", "pages": None,
            "chars": row.get("chars"), "in_translation": bool(row.get("in_translation")),
            "block": b})
    return out


def _chapters(drama: dict) -> dict:
    media = _media(drama)
    if media == "comic":
        return _comic_chapters(drama["id"])
    if media == "novel":
        return _novel_chapters(drama["id"])
    return {"readable": True, "chapters": []}


def _public_row(row: dict) -> dict:
    return {k: row[k] for k in ("id", "title", "pages", "chars")}


def list_chapters(drama_id, principal=None) -> dict:
    """This title's chapters, and the titles they could move to (same kind
    and original language, ones the caller may edit)."""
    drama = _require(drama_id, principal)
    media = _media(drama)
    info = _chapters(drama)
    targets = []
    if media is not None:
        for d in db.list_dramas():
            if (d["id"] == drama["id"] or _media(d) != media
                    or d.get("source_language") != drama.get("source_language")
                    or not ownership_service.can_edit_drama(principal, d["id"])):
                continue
            targets.append({"id": d["id"], "title": _name(d)})
            if len(targets) >= MAX_TARGETS:
                break
    return {"drama_id": drama["id"], "kind": media,
            "chapters": [_public_row(r) for r in info["chapters"]], "targets": targets}


# ---------------------------------------------------------------------------
# Plan (shared by preview and apply)
# ---------------------------------------------------------------------------

def _block_digest(drama_id: int, block: dict) -> bytes:
    h = hashlib.sha256()
    with open(chapter_manifest.raw_path(drama_id), "rb") as f:
        f.seek(block["start"])
        left = block["length"]
        while left > 0:
            data = f.read(min(_CHUNK, left))
            if not data:
                break
            h.update(data)
            left -= len(data)
    return h.digest()


def _mark_duplicates(media: str, source_id: int, target_id: int, src: dict, dst: dict) -> None:
    if media == "comic":
        have = {c["key"] for c in dst["manifest"]["chapters"]}
        for row in src["chapters"]:
            row["duplicate"] = bool(row["key"]) and row["key"] in have
        return
    by_length = {}
    urls = set()
    for row in dst["chapters"]:
        b = row["block"]
        by_length.setdefault(b["length"], []).append(b)
        if b.get("url"):
            urls.add(b["url"])
    for row in src["chapters"]:
        b = row["block"]
        dup = bool(b.get("url")) and b["url"] in urls
        if not dup and by_length.get(b["length"]):
            digest = _block_digest(source_id, b)
            dup = any(_block_digest(target_id, other) == digest
                      for other in by_length[b["length"]])
        row["duplicate"] = dup


def _hold_id(drama_id: int) -> str:
    return f"{job_launch.RESTRUCTURE_JOB_PREFIX}{int(drama_id)}"


def _blocked_reasons(kind, source, target, media, src, dst) -> list:
    reasons = []
    if media is None:
        reasons.append("Only comic and novel titles have chapters to move.")
    elif _media(target) != media:
        reasons.append("These titles are different kinds (a comic and a novel, or a title "
                       "without chapters), so their chapters can't be combined.")
    if (source.get("source_language") or "") != (target.get("source_language") or ""):
        reasons.append("These titles have different original languages.")
    for drama in (source, target):
        if drama_service.job_running_for_drama(drama["id"], exclude_job_id=_hold_id(drama["id"])):
            reasons.append(f"A job is running on \"{_name(drama)}\". Wait for it to finish or "
                           "cancel it first.")
    if not src.get("readable", True) or not dst.get("readable", True):
        reasons.append("A chapter file of one of these titles could not be read, so nothing "
                       "can be moved safely. Nothing was changed.")
    if kind == "merge":
        if source.get("audio_filename") or source.get("source_video_filename"):
            reasons.append("The old title has an audio or video file, which chapters can't "
                           "carry. Move chapters instead.")
        if db.load_line_ids(source["id"]):
            reasons.append("The old title has transcribed or translated lines, which chapters "
                           "can't carry. Move chapters instead.")
        if os.path.lexists(os.path.join(db.drama_dir(source["id"]), NOVEL_SOURCE_FILENAME)):
            reasons.append("The old title has translation text, which chapters can't carry. "
                           "Move chapters instead.")
    return reasons


def _state(kind, source, target, media, src, dst, chapter_ids, move_duplicates) -> dict:
    """Everything the plan depends on, hashed into the preview's fingerprint."""
    state = {"kind": kind, "source": source["id"], "target": target["id"], "media": media,
             "chapter_ids": sorted(chapter_ids or []), "move_duplicates": bool(move_duplicates),
             "source_row": [source.get(k) for k in ("media_type", "source_language", "series_id",
                                                    "audio_filename")],
             "target_row": [target.get(k) for k in ("media_type", "source_language", "series_id")]}
    if media == "comic":
        state["src_pages"] = [(p["id"], p["idx"], p["filename"], p.get("rendered_filename"))
                              for p in src["pages"]]
        state["dst_pages"] = [(p["id"], p["idx"]) for p in dst.get("pages", [])]
        state["manifests"] = [src["manifest"], dst.get("manifest")]
    elif media == "novel":
        state["files"] = [(src["size"], src["mtime"]), (dst.get("size"), dst.get("mtime"))]
        state["manifests"] = [chapter_manifest.load(source["id"]),
                              chapter_manifest.load(target["id"])]
    return state


def _plan(source_id, target_id, kind, chapter_ids, move_duplicates, principal) -> dict:
    if kind not in KINDS:
        raise InvalidInputError("kind must be \"merge\" or \"move\".")
    if source_id == target_id:
        raise InvalidInputError("Pick a different title to move chapters to.")
    source = _require(source_id, principal)
    target = _require(target_id, principal)
    if kind == "move":
        if (not isinstance(chapter_ids, (list, tuple)) or not chapter_ids
                or len(chapter_ids) > MAX_CHAPTER_IDS
                or not all(isinstance(c, str) for c in chapter_ids)):
            raise InvalidInputError("Pick at least one chapter to move.")
        if len(set(chapter_ids)) != len(chapter_ids):
            raise InvalidInputError("A chapter is listed twice.")
    media = _media(source)
    src = _chapters(source)
    dst = _chapters(target) if media is not None and _media(target) == media else {
        "readable": True, "chapters": []}
    known = {r["id"] for r in src["chapters"]}
    if kind == "move" and not set(chapter_ids) <= known:
        raise InvalidInputError("A chapter you picked is no longer in this title. Reload and "
                                "try again.")
    blocked = _blocked_reasons(kind, source, target, media, src, dst)
    if media is not None and _media(target) == media and src["readable"] and dst["readable"]:
        _mark_duplicates(media, source["id"], target["id"], src, dst)
    picked = set(chapter_ids) if kind == "move" else known
    for row in src["chapters"]:
        row.setdefault("duplicate", False)
        row["picked"] = row["id"] in picked
        row["moves"] = row["picked"] and (move_duplicates or not row["duplicate"])
    moving = [r for r in src["chapters"] if r["moves"]]
    skipped = sum(1 for r in src["chapters"] if r["picked"] and not r["moves"])
    if not moving and not blocked:
        blocked.append("There is nothing to move: every chapter picked is already in the "
                       "other title.")
    empty_after = len(moving) == len(src["chapters"])

    stays, notes = [], []
    if kind == "merge":
        stays.append("The old title keeps its details, cover, notes and reading progress. "
                     "Delete it from the Library when you're sure.")
    if skipped:
        stays.append(f"{skipped} chapter(s) already in \"{_name(target)}\" stay here.")
    copied = sum(1 for r in moving if r["in_translation"])
    if copied:
        stays.append(f"{copied} chapter(s) were already copied into this title's translation "
                     "text; that copy stays here.")
    if source.get("series_id") and source.get("series_id") != target.get("series_id"):
        notes.append("The titles are in different series. Glossary terms belong to the series, "
                     "so they don't move.")
    if kind == "merge":
        notes.append(f"\"{_name(target)}\" also gets this title's tags, and its source link if "
                     "it has none. Once empty, this title is tagged Merged, and the browser "
                     "extension stops picking it.")

    state = _state(kind, source, target, media, src, dst, chapter_ids, move_duplicates)
    state["blocked"] = blocked
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                                            default=str).encode("utf-8")).hexdigest()
    return {
        "source": source, "target": target, "kind": kind, "media": media, "src": src, "dst": dst,
        "moving": moving, "move_duplicates": bool(move_duplicates),
        "public": {
            "source_id": source["id"], "target_id": target["id"], "kind": kind,
            "media": media, "target_title": _name(target),
            "chapters": [{**_public_row(r), "duplicate": r["duplicate"], "moves": r["moves"],
                          "in_translation": r["in_translation"]}
                         for r in src["chapters"] if r["picked"]],
            "moving_chapters": len(moving),
            "moving_pages": sum(r["pages"] or 0 for r in moving),
            "skipped_duplicates": skipped,
            "source_empty_after": empty_after,
            "stays": stays, "notes": notes, "blocked": blocked, "fingerprint": fingerprint}}


def preview(source_id, target_id, kind, chapter_ids=None, move_duplicates=False,
            principal=None) -> dict:
    """What a merge or move would do. Writes nothing."""
    return _plan(source_id, target_id, kind, chapter_ids, move_duplicates, principal)["public"]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _snapshot_root() -> str:
    return os.path.join(db.LIBRARY_DIR, *SNAPSHOT_SUBDIR)


def _snapshot_files(plan) -> list:
    names = ([comic_chapters.MANIFEST_NAME] if plan["media"] == "comic" else
             [chapter_manifest.RAW_NOVEL_FILENAME, chapter_manifest.MANIFEST_FILENAME])
    return [(plan[side]["id"], name) for side in ("source", "target") for name in names]


def _take_snapshot(plan) -> str:
    root = _snapshot_root()
    os.makedirs(root, exist_ok=True)
    name = (f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{time.time_ns() % 10**9:09d}-"
            f"{plan['kind']}-{plan['source']['id']}-to-{plan['target']['id']}")
    folder = os.path.join(root, name)
    os.makedirs(folder)
    db.snapshot_database(os.path.join(folder, "library.db"))
    plan["existed"] = set()
    for drama_id, filename in _snapshot_files(plan):
        if os.path.lexists(os.path.join(db.drama_dir(drama_id), filename)):
            plan["existed"].add((drama_id, filename))
        found = chapter_manifest.safe_file(drama_id, filename)
        if found is not None:
            os.makedirs(os.path.join(folder, str(drama_id)), exist_ok=True)
            shutil.copy2(found[0], os.path.join(folder, str(drama_id), filename))
    return name


def _restore_from_snapshot(name: str, files, existed) -> None:
    """Puts the snapshot's copy of each (drama id, filename) back, or removes
    the file when it did not exist before (`existed`: those that did)."""
    folder = os.path.join(_snapshot_root(), name)
    for drama_id, filename in files:
        saved = os.path.join(folder, str(drama_id), filename)
        dest = os.path.join(db.drama_dir(drama_id), filename)
        if os.path.exists(saved):
            fd, tmp = tempfile.mkstemp(prefix=".restructure_", suffix=".part",
                                       dir=os.path.dirname(dest))
            os.close(fd)
            shutil.copy2(saved, tmp)
            os.replace(tmp, dest)
        elif (drama_id, filename) not in existed and os.path.lexists(dest):
            os.remove(dest)


def _prune_snapshots() -> None:
    root = _snapshot_root()
    try:
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
    except OSError:
        return
    for old in names[:max(0, len(names) - KEEP_SNAPSHOTS)]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)


# ---------------------------------------------------------------------------
# Comic move
# ---------------------------------------------------------------------------

def _place(src: str, dst: str) -> None:
    """A second name for the file when the file system allows it (no extra
    space, nothing to copy), else an exclusive copy. Never overwrites."""
    try:
        os.link(src, dst)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    with open(src, "rb") as f, open(dst, "xb") as out:
        shutil.copyfileobj(f, out, _CHUNK)


def _move_comic(plan) -> dict:
    a, b = plan["source"]["id"], plan["target"]["id"]
    pages_dir = os.path.join(db.drama_dir(b), "pages")
    os.makedirs(pages_dir, exist_ok=True)
    created, claims, moves, originals = [], [], [], []
    missing = 0
    try:
        idx = pipeline._next_page_index(b, pages_dir)
        for row in plan["moving"]:
            for page in row["page_rows"]:
                claim = pipeline._claim_page_index(pages_dir, idx)
                while claim is None:
                    idx += 1
                    claim = pipeline._claim_page_index(pages_dir, idx)
                claims.append(claim)
                ext = os.path.splitext(page["filename"] or "")[1] or ".png"
                new_name = os.path.join("pages", f"page_{idx:04d}{ext}")
                found = chapter_manifest.safe_file(a, page["filename"] or "")
                if found is None:
                    missing += 1
                else:
                    dst = os.path.join(db.drama_dir(b), new_name)
                    _place(found[0], dst)
                    created.append(dst)
                    originals.append(found[0])
                rendered = page.get("rendered_filename")
                if rendered:
                    found_r = chapter_manifest.safe_file(a, rendered)
                    if found_r is not None:
                        dst_r = os.path.join(db.drama_dir(b), rendered)
                        _place(found_r[0], dst_r)
                        created.append(dst_r)
                        originals.append(found_r[0])
                moves.append({"page_id": page["id"], "filename": page["filename"], "idx": idx,
                              "new_filename": new_name, "rendered_filename": rendered})
                idx += 1
        if not db.move_pages(a, b, moves):
            raise ConflictError("The pages changed while they were being moved. Nothing was "
                                "moved; reload and try again.")
    except BaseException as e:
        for path in created:
            with contextlib.suppress(OSError):
                os.remove(path)
        if isinstance(e, (ServiceError, KeyboardInterrupt, SystemExit)):
            raise
        log.warning("Moving pages from title %s to %s failed", a, b, exc_info=True)
        raise ServiceError("The pages could not be moved. Nothing was changed.") from e
    finally:
        for claim in claims:
            with contextlib.suppress(OSError):
                os.remove(claim)

    warnings = []
    renames = {m["filename"]: m["new_filename"] for m in moves}
    try:
        comic_chapters.move_labels(a, b, renames, plan["move_duplicates"])
    except OSError:
        log.warning("Could not move chapter labels from title %s to %s", a, b, exc_info=True)
        warnings.append(_LABELS_NOT_MOVED)
        # Page names freed in the source get reused by its next import, and a
        # label left on them would put that new page in the moved chapter.
        try:
            comic_chapters.forget_pages(a, list(renames))
        except OSError:
            log.warning("Could not drop moved chapter labels from title %s", a, exc_info=True)
    stuck = 0
    for path in originals:
        try:
            os.remove(path)
        except OSError:
            stuck += 1
    if stuck:
        warnings.append(_LEFTOVER_ORIGINALS)
    if missing:
        warnings.append(f"{missing} page image(s) were already missing from the old title; "
                        "those pages moved without an image.")
    chapters = []
    for c in plan["src"]["manifest"]["chapters"]:
        moved = [f for f in c["files"] if f in renames]
        if moved and c["id"] and c["key"].endswith(f":{c['id']}"):
            # A chapter split into runs can move one run and leave another;
            # both titles then hold part of it, so both keep the record.
            whole = len(moved) == len(c["files"])
            chapters.append((c["key"][:-len(c["id"]) - 1], c["id"], whole))
    return {"pages": len(moves), "warnings": warnings, "tracked_chapters": chapters}


# ---------------------------------------------------------------------------
# Novel move
# ---------------------------------------------------------------------------

def _swap_in(tmp: str, dest: str) -> None:
    """Replaces `dest` with the finished temp file in one step."""
    os.replace(tmp, dest)


def _copy_range(f, out, start: int, length: int) -> None:
    f.seek(start)
    left = length
    while left > 0:
        data = f.read(min(_CHUNK, left))
        if not data:
            raise OSError("The raw novel file is shorter than its chapter list.")
        out.write(data)
        left -= len(data)


def _temp_in(folder: str):
    fd, tmp = tempfile.mkstemp(prefix=".restructure_", suffix=".part", dir=folder)
    return os.fdopen(fd, "wb"), tmp


def _manifest_rows(drama_id: int, size: int, split: bool) -> list:
    """The target's chapter list as stored, before the move: its manifest
    while it matches the file, else the whole file as one unsplit entry."""
    if size <= 0:
        return []
    known = chapter_manifest.load(drama_id)
    if split and known:
        return [dict(c) for c in known["chapters"]]
    with open(chapter_manifest.raw_path(drama_id), "rb") as f:
        chars = chapter_manifest.count_chars(f, 0, size)
    return [{"title": "", "source": "", "imported_at": "", "url": "", "start": 0,
             "length": size, "chars": chars, "unsplit": True}]


def _write_text(folder: str, dest: str, text: str, temps: list) -> None:
    out, tmp = _temp_in(folder)
    temps.append(tmp)
    with out:
        out.write(text.encode("utf-8"))
        out.flush()
        os.fsync(out.fileno())
    _swap_in(tmp, dest)


def _move_novel(plan, snapshot: str) -> dict:
    a, b = plan["source"]["id"], plan["target"]["id"]
    sep = pipeline._as_written("\n\n")
    src, dst = plan["src"], plan["dst"]
    moving_ids = {r["id"] for r in plan["moving"]}
    raw_a = chapter_manifest.raw_path(a)
    folder_a, folder_b = db.drama_dir(a), db.drama_dir(b)
    raw_b = chapter_manifest.raw_path(b)
    stored_a = chapter_manifest.load(a) if src["split"] else None
    stored_a = stored_a["chapters"] if stored_a else [None] * len(src["chapters"])
    temps = []
    try:
        rows_b = _manifest_rows(b, dst.get("size", 0), dst.get("split", False))
        out, tmp_b = _temp_in(folder_b)
        temps.append(tmp_b)
        kept_a = []
        with out, open(raw_a, "rb") as f:
            pos = 0
            if dst.get("size", 0) > 0:
                with open(raw_b, "rb") as old:
                    shutil.copyfileobj(old, out, _CHUNK)
                pos = dst["size"]
            for row, stored in zip(src["chapters"], stored_a):
                if row["id"] not in moving_ids:
                    kept_a.append((row, stored))
                    continue
                block = row["block"]
                if pos > 0:
                    out.write(sep)
                    pos += len(sep)
                _copy_range(f, out, block["start"], block["length"])
                chars = row["chars"] if row["chars"] is not None else \
                    chapter_manifest.count_chars(f, block["start"], block["length"])
                # An unsplit block has no title of its own; in the target it
                # is one chapter among others, so it needs a readable one.
                title = (stored or {}).get("title") or f"Text from {_name(plan['source'])}"
                rows_b.append({
                    "title": title[:200], "source": (stored or {}).get("source", ""),
                    "imported_at": (stored or {}).get("imported_at") or time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "url": (stored or {}).get("url", ""), "start": pos,
                    "length": block["length"], "chars": chars, "unsplit": False})
                pos += block["length"]
            out.flush()
            os.fsync(out.fileno())
            size_b = pos

            out_a, tmp_a = _temp_in(folder_a)
            temps.append(tmp_a)
            rows_a, pos_a, prev_end = [], 0, None
            with out_a:
                for row, stored in kept_a:
                    block = row["block"]
                    if prev_end is not None:
                        # The original separator before this block, so the
                        # kept text reads back exactly as it did.
                        gap_start = _gap_start(src["chapters"], row)
                        _copy_range(f, out_a, gap_start, block["start"] - gap_start)
                        pos_a += block["start"] - gap_start
                    _copy_range(f, out_a, block["start"], block["length"])
                    rows_a.append({**(stored or {}), "start": pos_a,
                                   "length": block["length"],
                                   "chars": row["chars"] if row["chars"] is not None else
                                   (stored or {}).get("chars", 0)})
                    pos_a += block["length"]
                    prev_end = block["start"] + block["length"]
                last = src["chapters"][-1]["block"]
                tail = src["size"] - (last["start"] + last["length"])
                if rows_a and tail > 0:
                    _copy_range(f, out_a, last["start"] + last["length"], tail)
                    pos_a += tail
                out_a.flush()
                os.fsync(out_a.fileno())

        _swap_in(tmp_b, raw_b)
        _write_text(folder_b, chapter_manifest.manifest_path(b),
                    chapter_manifest.serialise(size_b, rows_b), temps)
        if rows_a:
            _swap_in(tmp_a, raw_a)
            if src["split"]:
                _write_text(folder_a, chapter_manifest.manifest_path(a),
                            chapter_manifest.serialise(pos_a, rows_a), temps)
        else:
            os.remove(tmp_a)
            os.remove(raw_a)
            if not chapter_manifest.drop(a):
                raise OSError("The old title's chapter list could not be removed.")
    except BaseException as e:
        for tmp in temps:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        try:
            _restore_from_snapshot(snapshot, _snapshot_files(plan), plan["existed"])
        except OSError:
            log.error("Could not put back the raw novel files of titles %s and %s from "
                      "snapshot %s", a, b, snapshot, exc_info=True)
            raise ServiceError("Moving the chapters failed and the files could not be put "
                               f"back automatically. A copy is in the snapshot \"{snapshot}\" "
                               "in the library's backups folder.") from e
        if isinstance(e, (ServiceError, KeyboardInterrupt, SystemExit)):
            raise
        log.warning("Moving novel chapters from title %s to %s failed", a, b, exc_info=True)
        raise ServiceError("The chapters could not be moved. Nothing was changed.") from e
    return {"pages": 0, "warnings": [], "tracked_chapters": []}


def _gap_start(rows: list, row: dict) -> int:
    """Where the separator before `row`'s block starts: the end of the block
    just before it in the original file."""
    i = rows.index(row)
    before = rows[i - 1]["block"]
    return before["start"] + before["length"]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _merge_title_fields(source: dict, target_id: int) -> None:
    target = db.get_drama(target_id)
    if target is None:
        raise LookupError("The other title is gone.")
    tags, seen = [], set()
    for t in ((target.get("custom_tags") or "") + "," + (source.get("custom_tags") or "")).split(","):
        t = t.strip()
        if t and t.lower() not in seen and t.lower() != db.MERGED_TAG.lower():
            seen.add(t.lower())
            tags.append(t)
    joined = ", ".join(tags)
    if joined != (target.get("custom_tags") or ""):
        db.update_drama(target_id, custom_tags=joined)
    link = source.get("source_url_full") or source.get("source_url")
    if link:
        drama_service.set_source_url_once(target_id, link)


def _move_import_records(old: int, new: int, chapters=None) -> None:
    """The source import records (sources.db) follow the chapters, so a
    later import into the new title skips them. `chapters`: (source,
    chapter_id, whole) per chapter that moved, `whole` False when part of it
    stayed (the record is then copied, not moved); None (a merge) moves them
    all, plus the failed-chapter list and any tracked series importing into
    the old title. Each row keeps its own series id; a record the new title
    already has is kept."""
    with store.connect() as conn:
        if chapters is None:
            chapters = [(r[0], r[1], True) for r in conn.execute(
                "SELECT DISTINCT source, chapter_id FROM imported_chapters WHERE drama_id=?",
                (old,))]
            for table in ("import_retry", "tracked_series"):
                conn.execute(f"UPDATE OR IGNORE {table} SET drama_id=? WHERE drama_id=?",
                             (new, old))
        for source, chapter_id, whole in chapters:
            key = (source, str(chapter_id), old)
            conn.execute("INSERT OR IGNORE INTO imported_chapters(source, series_id, chapter_id, "
                         "drama_id, imported_at) SELECT source, series_id, chapter_id, ?, "
                         "imported_at FROM imported_chapters WHERE source=? AND chapter_id=? "
                         "AND drama_id=?", (new,) + key)
            if whole:
                conn.execute("DELETE FROM imported_chapters WHERE source=? AND chapter_id=? "
                             "AND drama_id=?", key)


def _has_import_records(drama_id: int) -> bool:
    with store.connect() as conn:
        return conn.execute("SELECT 1 FROM imported_chapters WHERE drama_id=? LIMIT 1",
                            (drama_id,)).fetchone() is not None


@contextlib.contextmanager
def _job_holds(*drama_ids):
    """A running `restructure_<id>` job on each title for the whole move, so
    job starts (jobs/job_launch.py) and deleting either title are refused
    meanwhile, in this process and (through the job record) in others."""
    release = threading.Event()
    held = []
    try:
        for d in sorted(set(drama_ids)):
            if not job_launch.start_job(_hold_id(d), release.wait,
                                         description="Moving chapters between titles",
                                         line_fields=()):
                raise ConflictError("Chapters are already being moved to or from this title.")
            held.append(_hold_id(d))
        yield
    finally:
        release.set()
        # The hold's thread ends on its own; wait for it so an immediate next
        # move (or job) isn't refused by a hold that is just finishing.
        deadline = time.monotonic() + _HOLD_WAIT_SECONDS
        while any(background_jobs.is_running(j) for j in held) and time.monotonic() < deadline:
            time.sleep(0.005)


def apply(source_id, target_id, kind, chapter_ids=None, move_duplicates=False, fingerprint="",
          confirm=False, principal=None) -> dict:
    """Does what `preview` showed, if nothing changed since."""
    if confirm is not True:
        raise InvalidInputError("Moving chapters needs confirm=true.")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise InvalidInputError("Preview the change first.")
    for d in (source_id, target_id):
        _require(d, principal)
    with _job_holds(source_id, target_id), _page_locks(source_id, target_id):
        plan = _plan(source_id, target_id, kind, chapter_ids, move_duplicates, principal)
        public = plan["public"]
        if public["blocked"]:
            raise ConflictError(public["blocked"][0])
        if public["fingerprint"] != fingerprint:
            raise ConflictError("These titles changed since the preview. Preview again before "
                                "moving.")
        snapshot = _take_snapshot(plan)
        if plan["media"] == "comic":
            done = _move_comic(plan)
        else:
            done = _move_novel(plan, snapshot)
        warnings = list(done["warnings"])
        a, b = plan["source"]["id"], plan["target"]["id"]
        # Everything below runs after the move is committed: a failure here
        # (a title deleted meanwhile, a busy sources.db) is a warning, never
        # an error that would hide a move that happened.
        if kind == "merge":
            try:
                _merge_title_fields(plan["source"], b)
                # Not when skipped duplicates stayed behind: that title still holds chapters.
                if public["source_empty_after"]:
                    db.set_custom_tag(a, db.MERGED_TAG, True)
            except Exception:
                log.warning("Could not update titles %s and %s after a merge", a, b,
                            exc_info=True)
                warnings.append(_TITLE_FIELDS_NOT_MERGED)
        try:
            if kind == "merge":
                _move_import_records(a, b)
            elif done["tracked_chapters"]:
                _move_import_records(a, b, done["tracked_chapters"])
            elif plan["media"] == "novel" and _has_import_records(a):
                warnings.append(_NOVEL_TRACKING)
        except Exception:
            log.warning("Could not move source import records from title %s to %s", a, b,
                        exc_info=True)
            warnings.append(_TRACKING_NOT_MOVED)
    try:
        _prune_snapshots()
    except Exception:
        log.warning("Could not prune old restructure snapshots", exc_info=True)
    return {"source_id": a, "target_id": b, "kind": kind,
            "moved_chapters": public["moving_chapters"], "moved_pages": done["pages"],
            "skipped_duplicates": public["skipped_duplicates"],
            "source_empty": public["source_empty_after"], "snapshot": snapshot,
            "warnings": warnings}
