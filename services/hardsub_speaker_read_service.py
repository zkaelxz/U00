"""
services/hardsub_speaker_read_service.py -- reads a title's burned-in subtitles speaker by speaker with a
vision model. Across the title's spoken stretches, pictures of the subtitle band are taken, split into one
group per speaker (hardsub_speaker_groups) and each group is read on its own; a read counts when a second
picture agrees, and a refusal is never kept. The result is a reference line list per group, compared with
the title's transcript (hardsub_speaker_compare). Pictures are not kept. UI-free.
"""
import io
import json
import os
import shutil

from PIL import Image

import background_jobs
import db
from engine_backends import pricing, engine_registry
from jobs import job_launch
from lib.atomic_write import atomic_write
from services import benchmark_hardsub_read_service as reads
from services import benchmark_hardsub_service as band_pictures
from services import hardsub_speaker_compare, hardsub_speaker_groups, image_text_service, ownership_service
from services import settings_service, translate_run_service, translate_service
from services.benchmark_case_service import redact
from lib.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    UnsupportedOperationError,
)

JOB_PREFIX = "hardsub_reference_"
# One stretch of video is read from this many seconds of pictures, three pictures each, as in a case.
WINDOW_SECONDS = 3.0
FRAME_FRACTIONS = band_pictures.FRAME_FRACTIONS
# Only stretches this close to a transcript line are read: hard subs follow speech, and the rest of the
# video (credits, music) would cost reads for nothing.
SPEECH_PAD_SECONDS = 1.0
MAX_WINDOWS = 400
MAX_SPEAKER_GROUPS = 6
DEFAULT_SPEAKER_GROUPS = 4
# A crop smaller than this is padded with its own background colour: providers refuse or misread slivers.
MIN_CROP_EDGE = 64
# The estimate assumes the tallest common video (4:3) at the frame width cap, so it is never low.
WORST_ASPECT = 0.75
# Consecutive failed requests that end the run (a wrong key or a provider outage would otherwise be
# retried for every picture).
MAX_CONSECUTIVE_FAILURES = 5


def job_id(drama_id: int) -> str:
    return f"{JOB_PREFIX}{drama_id}"


def _windows(lines: list) -> list:
    """[(start, end)] stretches of WINDOW_SECONDS that lie near speech."""
    if not lines:
        return []
    first, last = min(ln["start"] for ln in lines), max(ln["end"] for ln in lines)
    windows, at = [], first
    while at < last:
        end = at + WINDOW_SECONDS
        if any(ln["end"] >= at - SPEECH_PAD_SECONDS and ln["start"] <= end + SPEECH_PAD_SECONDS for ln in lines):
            windows.append((round(at, 3), round(end, 3)))
        at = end
    return windows


def _checked(principal, drama_id: int, speaker_groups, band_top, band_bottom) -> tuple:
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    speaker_groups = DEFAULT_SPEAKER_GROUPS if speaker_groups is None else speaker_groups
    if isinstance(speaker_groups, bool) or not isinstance(speaker_groups, int) \
            or not 1 <= speaker_groups <= MAX_SPEAKER_GROUPS:
        raise InvalidInputError(f"Speaker groups must be between 1 and {MAX_SPEAKER_GROUPS}.")
    band = band_pictures.check_band(band_top, band_bottom)
    windows = _windows(db.load_lines(drama_id))
    if not windows:
        raise InvalidInputError("This title has no lines to place the subtitle reading around.")
    if len(windows) > MAX_WINDOWS:
        raise InvalidInputError(f"This title has {len(windows)} stretches to read; the most is {MAX_WINDOWS}.")
    return drama, speaker_groups, band, windows


def estimate(principal, drama_id: int, engine: str, model=None, speaker_groups=None,
             band_top=None, band_bottom=None) -> dict:
    """The most reading the whole title can cost: every stretch, every speaker group and all three pictures
    read. Real runs stop reading a group once two pictures agree, so they cost less."""
    _drama, groups, band, windows = _checked(principal, drama_id, speaker_groups, band_top, band_bottom)
    height = round(band_pictures.MAX_FRAME_WIDTH * WORST_ASPECT * (band[1] - band[0]))
    one = image_text_service.estimate(band_pictures.MAX_FRAME_WIDTH, height, engine, model)
    reads_at_most = len(windows) * groups * len(FRAME_FRACTIONS)
    total = round(one["estimated_usd"] * reads_at_most, 6)
    cap_left = one["cap_left_usd"]
    return {"engine": one["engine"], "model": one["model"], "windows": len(windows), "speaker_groups": groups,
            "reads_at_most": reads_at_most, "estimated_usd": total, "cap_applies": one["cap_applies"],
            "monthly_cap_usd": one["monthly_cap_usd"], "cap_left_usd": cap_left,
            "fits_cap": cap_left is None or total <= cap_left}


def start(principal, drama_id: int, engine: str, model=None, speaker_groups=None, band_top=None,
          band_bottom=None, max_cost_usd=None) -> dict:
    """Starts the reading as a background job; poll GET /api/jobs/{job_id} for its result."""
    drama, groups, band, windows = _checked(principal, drama_id, speaker_groups, band_top, band_bottom)
    priced = estimate(principal, drama_id, engine, model, groups, band_top, band_bottom)
    if max_cost_usd is not None and (isinstance(max_cost_usd, bool) or not isinstance(max_cost_usd, (int, float))
                                     or max_cost_usd < 0):
        raise InvalidInputError("The spending limit must be a number of dollars, 0 or more.")
    use_free_tier = engine == "gemini" and settings_service.resolve_gemini_free_tier(None)
    cap = None
    if priced["cap_applies"]:
        if max_cost_usd == 0:
            raise UnsupportedOperationError("This engine is paid, so a spending limit of 0 allows no reading.")
        # Without a limit of its own the run may spend no more than the estimate just shown.
        cap, refusal = pricing.resolve_cost_cap(
            max_cost_usd or priced["estimated_usd"], translate_run_service.month_cap_usd(), db.get_month_spend())
        if refusal:
            raise UnsupportedOperationError(refusal)
    video = band_pictures._source_video(drama_id, drama)
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError("ffmpeg is not installed or not on PATH.")
    api_key = translate_service.resolve_api_key(engine)
    if api_key is None:
        raise MissingKeyError(engine)
    reader = engine_registry.get_engine(
        engine, api_key, priced["model"], free_tier=use_free_tier,
        base_url=settings_service.ollama_url() if engine == "ollama" else None)
    language = drama.get("source_language") or "zh"
    jid = job_id(drama_id)
    started = job_launch.start_job(
        jid, _run, jid, drama_id, reader, engine, priced["model"], use_free_tier, video, band, windows,
        groups, cap, api_key, language, description="Hard-sub reading", line_fields=())
    if not started:
        raise ConflictError("A hard-sub reading is already going for this title.")
    return {"job_id": jid, "windows": len(windows), "estimated_usd": priced["estimated_usd"]}


RESULT_FILE = "hardsub_reading.json"


def _result_path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), RESULT_FILE)


def _save_result(drama_id: int, outcome: dict) -> None:
    """Kept with the title so a paid reading survives an app restart; a failed write never fails the job."""
    # A title deleted while its reading ran must not get its folder (and the captions in it) back, and the
    # suffix keeps the storage cleanup of "*.tmp" files from removing the write half way.
    if not db.get_drama(drama_id):
        return
    try:
        atomic_write(_result_path(drama_id), json.dumps(outcome, ensure_ascii=False), suffix=".json.part")
    except OSError:
        pass


def _saved_result(drama_id: int):
    try:
        with open(_result_path(drama_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def result(principal, drama_id: int) -> dict:
    """The title's latest reading: its progress while it runs, its result once it has finished (from the
    saved copy after a restart). The Jobs API carries neither the groups nor the comparison, so this is
    the one place they can be read."""
    ownership_service.require_visible(principal, "drama", drama_id)
    job = background_jobs.get_status(job_id(drama_id))
    if job is None:
        # Only with no job at all: a newer reading that failed must show as failed, not as yesterday's result.
        saved = _saved_result(drama_id)
        if saved is None:
            raise NotFoundError("No hard-sub reading has been run for this title.")
        return {"status": "done", "progress": 1.0, "message": "Done", "result": saved}
    return {"status": job["status"], "progress": job.get("progress"), "message": job.get("message"),
            "result": job.get("result")}


def _crop_bytes(band_image: Image.Image, box: tuple) -> bytes:
    crop = band_image.crop(box)
    if min(crop.size) < MIN_CROP_EDGE:
        canvas = Image.new("RGB", (max(crop.width, MIN_CROP_EDGE), max(crop.height, MIN_CROP_EDGE)),
                           crop.convert("RGB").getpixel((0, 0)))
        canvas.paste(crop, (0, 0))
        crop = canvas
    out = io.BytesIO()
    crop.save(out, "PNG")
    return out.getvalue()


def _frame_groups(video: str, at: float, band: tuple):
    """(sharpness, band picture, its speaker groups) for a clear picture at `at` seconds, else None. The
    file is removed as soon as it is decoded."""
    name = band_pictures._grab(video, at, band)
    path = os.path.join(db.BENCHMARK_DIR, name)
    try:
        # Brightness is not tested here: a quiet speaker's small caption is under the 1% of pixels the
        # case filter's brightness test looks at, and a band with no light text has no groups anyway.
        sharp = band_pictures.measure(path)[1]
        if sharp < band_pictures.MIN_SHARPNESS:
            return None
        with Image.open(path) as im:
            picture = im.convert("RGB")
        found = hardsub_speaker_groups.split_band(picture)
        return (sharp, picture, found) if found else None
    finally:
        reads.remove_files([name])


class _Spend:
    """Money spent so far and what is left of the run's cap (None = no cap)."""

    def __init__(self, cap):
        self.cap, self.spent = cap, 0.0

    def left(self):
        return None if self.cap is None else max(self.cap - self.spent, 0.0)


def _read_window(video, window, band, max_groups, reader, engine, model, use_free_tier, language, spend,
                 api_key, tally) -> dict:
    """{group key: confirmed text or None} for one stretch. Pictures are read sharpest first per group and
    reading stops once two agree. Raises _CapReached when the next read could pass the cap."""
    start, end = window
    pictures = []
    for fraction in FRAME_FRACTIONS:
        got = _frame_groups(video, start + (end - start) * fraction, band)
        if got:
            pictures.append(got)
        else:
            tally["unclear_pictures"] += 1
    pictures.sort(key=lambda p: -p[0])
    keys = []
    for _sharp, _picture, found in pictures:
        keys += [g.key for g in found if g.key not in keys]
    out = {}
    for key in keys[:max_groups]:
        texts = []
        for _sharp, picture, found in pictures:
            group = next((g for g in found if g.key == key), None)
            if group is None:
                continue
            try:
                got = image_text_service.read_stored_image(
                    reader, engine, model, _crop_bytes(picture, group.box), language, use_free_tier, spend.left())
            except Exception as exc:
                spend.spent += getattr(exc, "cost_usd", 0.0) or 0.0
                tally["failed_reads"] += 1
                tally["consecutive_failures"] += 1
                tally["last_error"] = redact(exc, api_key)
                if tally["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
                    raise _TooManyFailures() from None
                continue
            if got.get("over_budget"):
                raise _CapReached()
            tally["consecutive_failures"] = 0
            spend.spent += got["cost_usd"]
            text = (got["text"] or "").strip()
            texts.append(None if reads.looks_like_refusal(text) else text)
            if reads.confirmed(texts):
                break
        out[key] = reads.confirmed(texts)
    return out


class _CapReached(Exception):
    pass


class _TooManyFailures(Exception):
    pass


def _merge(per_window: list) -> list:
    """[{"key", "lines": [{"start", "end", "text"}]}] from [(window, {key: text})]: consecutive stretches
    in which a group shows the same text are one line."""
    by_key = {}
    for (start, end), found in per_window:
        for key, text in found.items():
            lines = by_key.setdefault(key, [])
            if not text:
                continue
            if lines and lines[-1]["end"] >= start and reads.agree(lines[-1]["text"], text):
                lines[-1]["end"] = end
            else:
                lines.append({"start": start, "end": end, "text": text})
    return [{"key": key, "lines": lines} for key, lines in sorted(by_key.items())]


def _run(jid, drama_id, reader, engine, model, use_free_tier, video, band, windows, max_groups, cap,
         api_key, language):
    spend = _Spend(cap)
    tally = {"unclear_pictures": 0, "failed_reads": 0, "consecutive_failures": 0, "last_error": None}
    per_window, stopped, note = [], None, None
    try:
        for i, window in enumerate(windows):
            if background_jobs.is_cancel_requested(jid):
                stopped = "cancelled"
                break
            background_jobs.update_progress(jid, i / len(windows), f"Reading {i + 1} of {len(windows)}")
            per_window.append((window, _read_window(video, window, band, max_groups, reader, engine, model,
                                                   use_free_tier, language, spend, api_key, tally)))
    except _CapReached:
        stopped = "cap"
    except _TooManyFailures:
        stopped, note = "failed", tally["last_error"]
    except DependencyUnavailableError as exc:
        stopped, note = "failed", redact(exc, api_key)
    groups = _merge(per_window)
    unreadable = sum(1 for _w, found in per_window for text in found.values() if not text)
    outcome = {
        "groups": groups, "windows_read": len(per_window), "windows_total": len(windows),
        "unreadable": unreadable, "unclear_pictures": tally["unclear_pictures"],
        "failed_reads": tally["failed_reads"], "cost_usd": round(spend.spent, 6),
        "stopped": stopped, "note": note,
        "comparison": hardsub_speaker_compare.compare(groups, db.load_lines(drama_id), language)}
    _save_result(drama_id, outcome)
    background_jobs.set_result(jid, outcome)
    background_jobs.update_progress(jid, 1.0, "Done" if stopped is None else "Stopped")
