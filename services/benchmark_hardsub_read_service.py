"""
services/benchmark_hardsub_read_service.py -- reading a hard-sub case: the vision model reads up to three
pictures of the subtitle band, a read counts only when a second picture agrees with it, and a refusal
is never kept as text. Scoring compares the read with every caption the case's time window could show.
UI-free; the run loop, cap and result rows stay in benchmark_lab_service.
"""
import difflib
import json
import os
import re

import db
from services import image_text_service, page_import_limits
from services.benchmark_scoring_service import score_output

# Two reads of one caption differ by a character or two when one picture is slightly blurred; below this
# they are different texts.
AGREE_RATIO = 0.85
# A reply this long is a caption, not an apology.
MAX_REFUSAL_CHARS = 200
_REFUSAL = re.compile(
    r"\b(i(?:'m| am) (?:sorry|unable|not able)|i apologi[sz]e|sorry,|"
    r"(?:cannot|can't|can not|unable to|not able to) (?:read|see|make out|transcribe|identify|determine)|"
    r"no (?:visible |readable |legible )?(?:text|subtitles?|captions?)\b|"
    r"(?:image|picture|photo) (?:is|appears|seems) (?:too |very )?(?:blurr?y|dark|unclear|illegible)|"
    r"as an ai)", re.IGNORECASE)


def load(case: dict):
    """The hard-sub details stored on a case, or None for any other case."""
    try:
        data = json.loads(case.get("hardsub") or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("frames") else None


def remove_files(names) -> None:
    for name in names:
        try:
            os.remove(os.path.join(db.BENCHMARK_DIR, os.path.basename(name)))
        except OSError:
            pass


def remove_frames(case: dict) -> None:
    """A deleted case takes its pictures with it."""
    remove_files(f["file"] for f in (load(case) or {}).get("frames", []))


_GENERATED_CLIP = re.compile(r"^clip_[0-9a-f]{16}\.(mp3|wav|m4a|flac|ogg)$")


def remove_case_files(case: dict) -> None:
    """The files of a deleted case. An uploaded clip's audio goes with its last case; older cases may point
    at files this app didn't generate (an OCR image, a hand-placed file), so only a clip-imported case or a
    generated clip name is ever removed."""
    remove_frames(case)
    name = case.get("input_filename")
    if not name or name != os.path.basename(name) or db.count_benchmark_cases_with_file(name):
        return
    if case.get("clip_key") or _GENERATED_CLIP.match(name):
        remove_files([name])


def looks_like_refusal(text: str) -> bool:
    """An apology or 'nothing to read' reply, or one without a single letter or digit: never stored as the
    text of a caption, whatever the language."""
    text = (text or "").strip()
    if not any(ch.isalnum() for ch in text):
        return True
    return len(text) <= MAX_REFUSAL_CHARS and bool(_REFUSAL.search(text))


def _same_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def agree(a: str, b: str) -> bool:
    a, b = _same_text(a), _same_text(b)
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= AGREE_RATIO


def confirmed(reads: list):
    """The text two pictures agree on (the sharper one's), or None. `reads` is [(text or None)] in order of
    sharpness, so the earlier of an agreeing pair is the sharper."""
    for i, first in enumerate(reads):
        for second in reads[i + 1:]:
            if first and second and agree(first, second):
                return first
    return None


def _picture(frame: dict) -> bytes:
    path = os.path.join(db.BENCHMARK_DIR, os.path.basename(frame["file"]))
    if os.path.getsize(path) > page_import_limits.MAX_IMAGE_BYTES:
        raise ValueError("This picture is over the size limit.")
    with open(path, "rb") as fh:
        return fh.read(page_import_limits.MAX_IMAGE_BYTES + 1)


def read_case(engine, cfg: dict, case: dict, details: dict, use_free_tier: bool, budget, cancelled) -> dict:
    """{"text": the confirmed read or None, "cost_usd", "reads": how many pictures were read} or
    {"over_budget": True, "cost_usd"}. Pictures are read sharpest first and reading stops once two agree,
    so a clean caption costs two reads, not three. A failed request raises with the cost of the reads
    already paid for in `cost_usd`."""
    cost, reads = 0.0, []
    for frame in sorted(details["frames"], key=lambda f: -f["sharp"]):
        if cancelled():
            break
        try:
            got = image_text_service.read_stored_image(
                engine, cfg["engine"], cfg["model"], _picture(frame), "", use_free_tier,
                None if budget is None else max(budget - cost, 0.0))
        except Exception as exc:
            setattr(exc, "cost_usd", cost + (getattr(exc, "cost_usd", 0.0) or 0.0))
            raise
        if got.get("over_budget"):
            return {"over_budget": True, "cost_usd": cost}
        cost += got["cost_usd"]
        text = (got["text"] or "").strip()
        reads.append(None if looks_like_refusal(text) else text)
        if confirmed(reads):
            break
    return {"text": confirmed(reads), "cost_usd": cost, "reads": len(reads)}


# Stored as the result's error: the result row keeps no separate flag, so readers tell it from an
# engine error by this text.
UNREADABLE_ERROR = "Unreadable: the subtitle did not read the same in two pictures."


def is_unreadable(error) -> bool:
    return error == UNREADABLE_ERROR


def window_score(text: str, case: dict, details: dict, stage: str = "ocr"):
    """(score, metric, scorer) of a read against the captions the case's time window could show: each
    line alone and all of them joined, since a burned-in caption can span several lines or sit one
    line off the speech. The best match counts."""
    # The main line's text is the case's reference, which an edit may have fixed since the window was saved.
    texts = [case.get("reference_text") or w["text"] if w.get("main") else w["text"]
             for w in details.get("window") or []]
    candidates = list(dict.fromkeys(texts + [" ".join(texts)])) if texts else [case.get("reference_text")]
    scored = [score_output(stage, text, ref, case.get("source_language")) for ref in candidates if ref]
    return max(scored, key=lambda s: s[0] or 0.0) if scored else score_output(stage, text, None)
