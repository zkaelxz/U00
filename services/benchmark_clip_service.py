"""
services/benchmark_clip_service.py -- the owner's clip set for the Benchmark
Lab: uploaded audio clips with a transcript, an optional translation, a
language, a title and tags, plus the clean-up controls on cases (exclude,
edit a reference). UI-free.

Each clip makes a transcription case (the audio, scored against its
transcript) and, when it has a translation, a translation case (the
transcript translated, scored against that translation). Both keep the
clip's title, tags and scene order, and share a clip_key so a corrected
transcript reaches the translation case's source text too.

The same import takes a manifest (JSON lines, one per clip, naming the
audio file) in place of typed rows. Audio is copied into db.BENCHMARK_DIR
under a generated name; no path or client file name is stored or returned.
"""
import json
import os
import re
import time
import uuid

import db
from core import SOURCE_LANGUAGES
from services.benchmark_lab_service import (MAX_IMPORT_CASES, MAX_LABEL_CHARS, MAX_SET_NAME_CHARS,
                                            _case_out, _clean_text, _refuse_while_running,
                                            _stored_case_out)
from services.media_upload_service import AUDIO_EXTENSIONS, check_room_for
from lib.errors import InvalidInputError, NotFoundError

# A clip makes at most two cases, and an import is held to MAX_IMPORT_CASES.
MAX_CLIPS = MAX_IMPORT_CASES // 2
MAX_CLIP_BYTES = 50 * 1024 * 1024
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
MAX_TITLE_CHARS = 80
MAX_TAGS = 10
MAX_TAG_CHARS = 30
MAX_MANIFEST_BYTES = 2_000_000
_CHUNK = 1024 * 1024
_KANA = re.compile("[぀-ゟ゠-ヿㇰ-ㇿｦ-ﾟ]")
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힯]")
_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}


def language_warning(language: str, transcript: str):
    """A sentence when the transcript's script contradicts its language
    label, else None. Kana only occurs in Japanese and Hangul only in
    Korean; the label is never changed, only reported."""
    guess = "ko" if _HANGUL.search(transcript or "") else "ja" if _KANA.search(transcript or "") else None
    if guess is None or guess == language:
        return None
    return (f"Labelled {_LANGUAGE_NAMES.get(language, language)}, but the transcript has "
            f"{'Hangul' if guess == 'ko' else 'kana'}, which points to {_LANGUAGE_NAMES[guess]}.")


def _name(row: dict, position: int) -> str:
    """How a clip is named in a message: its file name (never the client's
    folders, which can carry a user name), else its place."""
    base = re.split(r"[\\/]", str(row.get("filename") or ""))[-1]
    return base[:MAX_LABEL_CHARS] or f"Clip {position}"


def _clean_tags(raw, who: str) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [t for t in re.split(r"[,;，；]", raw)]
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise InvalidInputError(f"{who}: tags must be a list of words.")
    tags = []
    for t in raw:
        t = t.strip()
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    if len(tags) > MAX_TAGS or any(len(t) > MAX_TAG_CHARS for t in tags):
        raise InvalidInputError(
            f"{who}: at most {MAX_TAGS} tags of {MAX_TAG_CHARS} characters each.")
    return tags


def _clean_row(row, position: int) -> dict:
    who = _name(row if isinstance(row, dict) else {}, position)
    if not isinstance(row, dict):
        raise InvalidInputError(f"{who}: expected an object.")
    language = row.get("language")
    if language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"{who}: language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    try:
        transcript = _clean_text(row.get("transcript"), "transcript")
        translation = _clean_text(row.get("translation"), "translation", required=False) or None
        title = _clean_text(row.get("title"), "title", required=False, max_len=MAX_TITLE_CHARS)
    except InvalidInputError as exc:
        raise InvalidInputError(f"{who}: {exc}") from None
    scene = row.get("scene_order")
    if scene is not None and (isinstance(scene, bool) or not isinstance(scene, int) or scene < 0):
        raise InvalidInputError(f"{who}: scene_order must be a whole number.")
    return {"filename": row.get("filename"), "language": language, "transcript": transcript,
            "translation": translation, "title": title, "scene_order": scene,
            "tags": _clean_tags(row.get("tags"), who)}


def parse_manifest(text: str) -> list:
    """Clip rows from a manifest: one JSON object per line with "audio" (the
    file's name), "language", "transcript" and optionally "translation",
    "title", "tags" and "scene_order". Blank lines and # comments are skipped."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise InvalidInputError("The manifest is too large.")
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise InvalidInputError(f"Manifest line {n} isn't valid JSON.") from None
        if not isinstance(obj, dict) or not isinstance(obj.get("audio"), str):
            raise InvalidInputError(f"Manifest line {n} needs an \"audio\" file name.")
        rows.append({**obj, "filename": obj["audio"]})
        if len(rows) > MAX_CLIPS:
            raise InvalidInputError(f"At most {MAX_CLIPS} clips per import.")
    if not rows:
        raise InvalidInputError("The manifest has no clips.")
    return rows


def check_clips(rows) -> dict:
    """Problems that would refuse the import and mismatch warnings, without
    saving anything (the table calls this as the owner types)."""
    if not isinstance(rows, list) or len(rows) > MAX_CLIPS:
        raise InvalidInputError(f"Send 1 to {MAX_CLIPS} clips.")
    problems, warnings = [], []
    for i, row in enumerate(rows, 1):
        try:
            clean = _clean_row(row, i)
        except InvalidInputError as exc:
            problems.append({"clip": _name(row if isinstance(row, dict) else {}, i), "message": str(exc)})
            continue
        message = language_warning(clean["language"], clean["transcript"])
        if message:
            warnings.append({"clip": _name(row, i), "message": message})
    return {"problems": problems, "warnings": warnings}


def _file_extension(client_name: str) -> str:
    ext = os.path.splitext(os.path.basename(str(client_name or "").replace("\\", "/")))[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        raise InvalidInputError(f"{client_name or 'A file'} isn't an audio file "
                                f"({', '.join(AUDIO_EXTENSIONS)}).")
    return ext


def _copy_clip(source, ext: str) -> str:
    """Writes one upload into the clip folder under a generated name and
    returns that name. The part file is removed on any failure."""
    name = f"clip_{uuid.uuid4().hex[:16]}{ext}"
    final = os.path.join(db.BENCHMARK_DIR, name)
    part = final + ".part"
    size = 0
    try:
        with open(part, "wb") as out:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_CLIP_BYTES:
                    raise InvalidInputError(
                        f"A clip is larger than {MAX_CLIP_BYTES // (1024 * 1024)} MB.")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, final)
    except BaseException:
        for leftover in (part, final):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise
    return name


def _stored_size(fileobj) -> int:
    here = fileobj.tell()
    size = fileobj.seek(0, os.SEEK_END)
    fileobj.seek(here)
    return size - here


def _plan(rows, files: dict):
    """(clean rows matched to their upload, warnings). Refuses before any
    file is written: a row without its audio, two rows for one file, a bad
    type or size. A file no row names is left out and reported."""
    if not isinstance(rows, list) or not rows:
        raise InvalidInputError("Add at least one clip.")
    if len(rows) > MAX_CLIPS:
        raise InvalidInputError(f"At most {MAX_CLIPS} clips per import.")
    clean, used, warnings, total = [], set(), [], 0
    for i, row in enumerate(rows, 1):
        item = _clean_row(row, i)
        who = _name(item, i)
        key = os.path.basename(str(item["filename"] or "").replace("\\", "/"))
        if key not in files:
            raise InvalidInputError(f"{who}: its audio file wasn't sent.")
        if key in used:
            raise InvalidInputError(f"{who}: another row already uses this file.")
        used.add(key)
        ext = _file_extension(key)
        size = _stored_size(files[key])
        if size > MAX_CLIP_BYTES:
            raise InvalidInputError(f"{who}: the clip is larger than {MAX_CLIP_BYTES // (1024 * 1024)} MB.")
        total += size
        message = language_warning(item["language"], item["transcript"])
        if message:
            warnings.append({"clip": who, "message": message})
        clean.append({**item, "key": key, "ext": ext, "who": who})
    if total > MAX_IMPORT_BYTES:
        raise InvalidInputError(f"The clips total more than {MAX_IMPORT_BYTES // (1024 * 1024)} MB.")
    warnings.extend({"clip": name, "message": "No row names this file; it was not imported."}
                    for name in files if name not in used)
    return clean, warnings, total


def _scene_orders(clean: list) -> None:
    """Fills a missing scene order with the clip's place among its title's
    clips, in the order given, so a scene keeps its sequence."""
    count = {}
    for item in clean:
        count[item["title"]] = count.get(item["title"], 0) + 1
        if item["scene_order"] is None:
            item["scene_order"] = count[item["title"]]


_GENERATED = re.compile(r"^clip_[0-9a-f]{16}\.(?:[a-z0-9]+)(?:\.part)?$")
_STALE_SECONDS = 3600


def _sweep_leftovers():
    """Removes clip files a crashed import left behind: part files and
    generated clips no case uses. The age floor keeps a concurrent import's
    files safe."""
    try:
        names = os.listdir(db.BENCHMARK_DIR)
    except OSError:
        return
    cutoff = time.time() - _STALE_SECONDS
    for name in names:
        path = os.path.join(db.BENCHMARK_DIR, name)
        try:
            if (_GENERATED.match(name) and os.path.getmtime(path) < cutoff
                    and (name.endswith(".part") or not db.count_benchmark_cases_with_file(name))):
                os.remove(path)
        except OSError:
            pass


def import_clips(set_name: str, rows, files: dict) -> dict:
    """Adds the clips to a set. `files` maps each upload's file name to a
    binary file object. Nothing is written when any row is refused; a
    failure while saving removes the audio already copied."""
    _refuse_while_running()
    set_name = _clean_text(set_name, "set_name", max_len=MAX_SET_NAME_CHARS)
    clean, warnings, total = _plan(rows, files)
    _scene_orders(clean)
    os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
    _sweep_leftovers()
    check_room_for(db.BENCHMARK_DIR, total)
    written, cases = [], []
    try:
        for item in clean:
            name = _copy_clip(files[item["key"]], item["ext"])
            written.append(name)
            clip_key = uuid.uuid4().hex[:12]
            label = f"{item['title']} #{item['scene_order']}" if item["title"] else os.path.splitext(item["key"])[0]
            common = {"label": label[:MAX_LABEL_CHARS], "content_type": "audio_drama",
                      "source_language": item["language"], "tier": "application",
                      "set_name": set_name, "title": item["title"], "scene_order": item["scene_order"],
                      "tags": json.dumps(item["tags"], ensure_ascii=False), "clip_key": clip_key}
            cases.append({**common, "stage": "transcription", "input_filename": name,
                          "reference_text": item["transcript"]})
            if item["translation"]:
                cases.append({**common, "stage": "translation", "source_text": item["transcript"],
                              "reference_text": item["translation"]})
        ids = db.create_benchmark_clip_cases(cases)
    except BaseException:
        for name in written:
            try:
                os.remove(os.path.join(db.BENCHMARK_DIR, name))
            except OSError:
                pass
        raise
    transcription = sum(1 for c in cases if c["stage"] == "transcription")
    return {"set_name": set_name, "clips": len(clean), "transcription_cases": transcription,
            "translation_cases": len(cases) - transcription, "case_ids": ids, "warnings": warnings}


def set_excluded(case_id: int, excluded: bool) -> dict:
    """An excluded case drops out of scoring (new runs and comparisons) and
    keeps its results; including it again brings it back."""
    _refuse_while_running()
    if not db.get_benchmark_case(case_id):
        raise NotFoundError("Benchmark case not found.")
    db.set_benchmark_case_excluded(case_id, excluded)
    return _stored_case_out(case_id)


def edit_reference(case_id: int, reference_text: str) -> dict:
    """Fixes a case's reference (a transcript or a translation) in place, so
    its past results stay tied to it. The change is logged and the case's
    reference version goes up; a corrected transcript also becomes the
    source text of the same clip's translation case."""
    _refuse_while_running()
    case = db.get_benchmark_case(case_id)
    if not case:
        raise NotFoundError("Benchmark case not found.")
    reference_text = _clean_text(reference_text, "reference_text")
    if reference_text == (case.get("reference_text") or ""):
        return _case_out(case)
    db.update_benchmark_case_texts(case_id, case.get("source_text"), reference_text)
    if case.get("stage") == "transcription" and case.get("clip_key"):
        for other in db.list_benchmark_cases_by_clip(case["clip_key"]):
            if other["id"] != case_id and other.get("stage") == "translation":
                db.update_benchmark_case_texts(other["id"], reference_text, other.get("reference_text"))
    return _stored_case_out(case_id)


def list_edits(case_id: int) -> dict:
    if not db.get_benchmark_case(case_id):
        raise NotFoundError("Benchmark case not found.")
    return {"edits": [{"field": e["field"], "old_text": e.get("old_text"), "new_text": e.get("new_text"),
                       "version": e["version"], "edited_at": e.get("edited_at")}
                      for e in db.list_benchmark_case_edits(case_id)]}

