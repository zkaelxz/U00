"""
services/benchmark_hardsub_service.py -- hard-sub test cases for the Benchmark Lab's OCR stage: for a
chosen line of a title, three pictures of the burned-in subtitle band are cut from the title's video and
stored as one case. Reading them (with a vision model the owner picks per run) is
benchmark_hardsub_read_service. UI-free.
"""
import json
import os
import shutil
import subprocess
import uuid

import numpy
from PIL import Image

import db
import video_export
from core import SOURCE_LANGUAGES
from lib.safe_paths import plain_file_in
from services import benchmark_lab_service as lab
from services import ownership_service
from services.benchmark_hardsub_read_service import remove_files, remove_frames
from services.media_upload_service import check_room_for
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError

SET_NAME = "hard-sub"
# Where in the line's time span the three pictures are taken. A hard sub often appears a moment after the
# line starts and goes before it ends, so the middle is weighted and the edges are kept off.
FRAME_FRACTIONS = (0.2, 0.5, 0.8)
# The band of the picture that is kept, as fractions of its height measured from the top: the bottom 30%
# by default, since that is where burned-in subtitles sit.
DEFAULT_BAND = (0.7, 1.0)
# Below this a band is only a sliver of the picture: no subtitle line fits in it.
MIN_BAND_HEIGHT = 0.1
MAX_FRAME_WIDTH = 1280
FRAME_TIMEOUT_SECONDS = 30
# A PNG of a 1280-wide band stays far under this; a larger file is a pathological source, not a subtitle.
MAX_FRAME_BYTES = 2_000_000
# Light subtitle text on a dark picture makes a few bright pixels, so the brightest 1% of the band is the
# measure: a band whose brightest pixels are still dim holds no light text to read. (A mean would reject a
# readable white subtitle on a black band.)
BRIGHT_PERCENTILE = 99
MIN_BRIGHT_LEVEL = 90
# Variance of the Laplacian of the band in grey levels: sharp text edges score in the hundreds, a motion-
# blurred or out-of-focus band falls below this.
MIN_SHARPNESS = 20.0
# Neighbouring lines whose time overlaps this much around the line are candidates for what the hard sub
# shows, because a burned-in caption can lag or lead the speech by a line.
WINDOW_PAD_SECONDS = 4.0
MAX_LINES_PER_REQUEST = 10
MAX_CASES_PER_TITLE = 60


def check_band(top, bottom) -> tuple:
    """(top, bottom) as fractions of the picture's height; never pixel values from the client."""
    top, bottom = DEFAULT_BAND if top is None and bottom is None else (top, bottom)
    if isinstance(top, bool) or isinstance(bottom, bool) or not all(isinstance(v, (int, float)) for v in (top, bottom)):
        raise InvalidInputError("The subtitle band must be fractions of the picture's height.")
    if not 0.0 <= top < bottom <= 1.0 or bottom - top < MIN_BAND_HEIGHT:
        raise InvalidInputError("The subtitle band must lie inside the picture and be at least a tenth of its height.")
    return float(top), float(bottom)


def measure(path: str) -> tuple:
    """(bright level, sharpness) of a stored picture; the two numbers the clarity filter uses."""
    with Image.open(path) as im:
        grey = numpy.asarray(im.convert("L"), dtype=numpy.float64)
    if grey.shape[0] < 3 or grey.shape[1] < 3:
        return 0.0, 0.0
    laplacian = (4 * grey[1:-1, 1:-1] - grey[:-2, 1:-1] - grey[2:, 1:-1] - grey[1:-1, :-2] - grey[1:-1, 2:])
    return float(numpy.percentile(grey, BRIGHT_PERCENTILE)), float(laplacian.var())


def is_clear(bright: float, sharpness: float) -> bool:
    return bright >= MIN_BRIGHT_LEVEL and sharpness >= MIN_SHARPNESS


def _grab(video: str, at: float, band: tuple) -> str:
    """Writes one picture of the band at `at` seconds into the case folder and returns its name; a failed
    grab leaves no file. The error names no path (ffmpeg's own text carries the command line)."""
    name = f"frame_{uuid.uuid4().hex[:16]}.png"
    final = os.path.join(db.BENCHMARK_DIR, name)
    part = final + ".part"
    top, bottom = band
    crop = f"crop=iw:ih*{bottom - top:.4f}:0:ih*{top:.4f}"
    cmd = ["ffmpeg", "-v", "error", "-y", *video_export.local_input(), "-ss", f"{at:.3f}", "-i", video,
           "-frames:v", "1", "-an", "-vf", f"{crop},scale='min({MAX_FRAME_WIDTH},iw)':-2",
           "-map_metadata", "-1", "-f", "image2", "-c:v", "png", part]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=FRAME_TIMEOUT_SECONDS)
        if not 0 < os.path.getsize(part) <= MAX_FRAME_BYTES:
            raise OSError("bad frame size")
        os.replace(part, final)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        for leftover in (part, final):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise DependencyUnavailableError("ffmpeg could not grab a picture from this title's video.") from None
    return name


def _window(lines: list, line: dict) -> list:
    """The line and the neighbours whose time falls near it, in order, as the texts a caption can show."""
    near = [ln for ln in lines if (ln.get("zh") or "").strip()
            and ln["end"] >= line["start"] - WINDOW_PAD_SECONDS and ln["start"] <= line["end"] + WINDOW_PAD_SECONDS]
    return [{"text": ln["zh"].strip(), "en": (ln.get("en") or "").strip(), "main": ln["id"] == line["id"]}
            for ln in near]


def _frames_for(video: str, line: dict, band: tuple) -> list:
    """The pictures that pass the clarity filter, as [{file, t, sharp}]; the others are not kept."""
    span = line["end"] - line["start"]
    kept = []
    try:
        for fraction in FRAME_FRACTIONS:
            at = line["start"] + span * fraction
            name = _grab(video, at, band)
            bright, sharp = measure(os.path.join(db.BENCHMARK_DIR, name))
            if is_clear(bright, sharp):
                kept.append({"file": name, "t": round(at, 3), "sharp": round(sharp, 2)})
            else:
                remove_files([name])
    except BaseException:
        remove_files(f["file"] for f in kept)
        raise
    return kept


def _source_video(drama_id: int, drama: dict) -> str:
    path = plain_file_in(db.drama_dir(drama_id), drama.get("source_video_filename") or "")
    if not path or not os.path.isfile(path):
        raise InvalidInputError("This title has no video to take pictures from.")
    return path


def add_lines(principal, drama_id: int, line_ids: list, set_name: str = SET_NAME, band_top=None,
              band_bottom=None) -> dict:
    """Adds an OCR-stage case for each chosen line: three pictures of the subtitle band across the line's
    time, the line's source text as the reference, its English as the reference translation when it has
    one. A line already in the set is skipped; a line none of whose pictures is clear is counted, not added."""
    band = check_band(band_top, band_bottom)
    set_name = lab._clean_text(set_name, "set_name", max_len=lab.MAX_SET_NAME_CHARS)
    if not line_ids or len(set(line_ids)) > MAX_LINES_PER_REQUEST:
        raise InvalidInputError(f"Pick between 1 and {MAX_LINES_PER_REQUEST} lines.")
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    lab._refuse_while_running()
    lines = db.load_lines(drama_id)
    by_id = {ln["id"]: ln for ln in lines}
    chosen = [by_id[i] for i in dict.fromkeys(line_ids) if i in by_id]
    if len(chosen) != len(set(line_ids)):
        raise NotFoundError("Line not found.")
    video = _source_video(drama_id, drama)
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError("ffmpeg is not installed or not on PATH.")
    existing = [c for c in db.list_benchmark_cases("ocr") if c.get("origin_drama_id") == drama_id]
    have = {c.get("origin_line_id") for c in existing if c.get("set_name") == set_name}
    fresh = [ln for ln in chosen if ln["id"] not in have]
    if len(existing) + len(fresh) > MAX_CASES_PER_TITLE:
        raise InvalidInputError(f"A title holds at most {MAX_CASES_PER_TITLE} hard-sub cases.")
    for ln in fresh:
        if not (ln.get("zh") or "").strip() or not 0 < ln["end"] - ln["start"] <= 60:
            raise InvalidInputError(f"Line {ln['idx'] + 1} has no text or no usable time span.")
    os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
    check_room_for(db.BENCHMARK_DIR, len(fresh) * len(FRAME_FRACTIONS) * MAX_FRAME_BYTES)
    title = (drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}")[:60]
    language = drama.get("source_language") if drama.get("source_language") in SOURCE_LANGUAGES else "zh"
    rows, unclear = [], 0
    try:
        for ln in fresh:
            frames = _frames_for(video, ln, band)
            if not frames:
                unclear += 1
                continue
            sharpest = max(frames, key=lambda f: f["sharp"])
            translation = (ln.get("en") or "").strip()
            rows.append({
                "label": f"{title} · line {ln['idx'] + 1} · hard sub", "stage": "ocr", "content_type": "hardsub",
                "source_language": language, "input_filename": sharpest["file"],
                "reference_text": ln["zh"].strip(), "tier": "application", "set_name": set_name, "title": title,
                "scene_order": ln["idx"] + 1, "origin_drama_id": drama_id, "origin_line_id": ln["id"],
                "hardsub": json.dumps({"frames": frames, "band": list(band), "window": _window(lines, ln),
                                       "translation": translation or None,
                                       "translation_source": "line" if translation else None})})
        db.create_benchmark_clip_cases(rows)
    except BaseException:
        for row in rows:
            remove_frames(row)
        raise
    if unclear and not rows:
        raise InvalidInputError("No clear picture of this subtitle could be taken. It may be too dark or blurred.")
    return {"set_name": set_name, "added": len(rows), "skipped": len(chosen) - len(fresh), "unclear": unclear}
