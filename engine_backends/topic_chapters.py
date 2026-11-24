"""Timed topic chapters for a long title: the prompt, the reply parser and the timestamp formats."""
import math
import re

from .line_text import extract_first_json_value

MAX_CHAPTERS = 40
MAX_TITLE_CHARS = 80
# YouTube ignores chapters closer than 10 s, and a chapter that short is not a topic anyway.
MIN_GAP_SECONDS = 30.0
TRANSCRIPT_MAX_CHARS = 60_000
_BUCKET_LINE_CHARS = 140
TOPIC_CHAPTERS_MAX_TOKENS = 2500


def format_clock(seconds: float) -> str:
    """m:ss, or h:mm:ss from an hour on: what YouTube reads as a chapter start."""
    total = max(0, int(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_clock(value) -> float | None:
    """Seconds from 'h:mm:ss', 'm:ss' or a plain number; None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, str) or not re.fullmatch(r"\s*\d+(:\d{1,2}){0,2}(\.\d+)?\s*", value):
        return None
    total = 0.0
    for part in value.strip().split(":"):
        total = total * 60 + float(part)
    return total


def _bucketed_rows(lines, bucket: float) -> list[str]:
    rows: list[str] = []
    current_key: int = 0
    texts: list[str] = []
    for ln in lines:
        text = ((ln.en or "").strip() or (ln.zh or "").strip())
        if not text:
            continue
        key = int(ln.start // bucket)
        if key != current_key and texts:
            rows.append(f"[{format_clock(current_key * bucket)}] " + " ".join(texts)[:_BUCKET_LINE_CHARS])
            texts = []
        current_key = key
        texts.append(text)
    if texts:
        rows.append(f"[{format_clock(current_key * bucket)}] " + " ".join(texts)[:_BUCKET_LINE_CHARS])
    return rows


def transcript_for_prompt(lines) -> str:
    """One row per time bucket, widened until the whole stream fits: chapters need the
    middle of a four-hour stream too, so unlike the summary nothing is cut from it."""
    bucket = 30.0
    while True:
        body = "\n".join(_bucketed_rows(lines, bucket))
        if len(body) <= TRANSCRIPT_MAX_CHARS or bucket >= 1800:
            return body[:TRANSCRIPT_MAX_CHARS]
        bucket *= 2


def build_prompt(lines, duration: float) -> str:
    target = max(3, min(MAX_CHAPTERS, round(duration / 600)))
    return (
        "Below is a timed transcript of one long video or stream: one row per stretch of time, "
        "starting with its [time]. Split it into topic chapters the way a viewer would want to "
        f"jump around it: about {target} chapters, each a clear change of topic, game, activity "
        "or conversation. The first chapter starts at 0:00. Each title is 2 to 8 words in "
        "English, plain and specific (no clickbait, no emoji, no timestamps in the title).\n\n"
        'Return ONLY a JSON object: {"chapters": [{"start": "m:ss", "title": "..."}]}, '
        "in time order, using start times that appear in the transcript. No markdown fences.\n\n"
        + transcript_for_prompt(lines)
    )


def parse_reply(text: str, duration: float) -> list[dict]:
    """Clean chapters from the engine's reply: in order, inside the video, at least
    MIN_GAP_SECONDS apart, the first at 0. [] when the reply holds none."""
    data = extract_first_json_value(text or "", expect=dict)
    raw = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        start = parse_clock(item.get("start"))
        title = " ".join(item["title"].split())[:MAX_TITLE_CHARS]
        if start is None or not title or (duration and start >= duration):
            continue
        found.append({"start": round(start, 1), "title": title})
    found.sort(key=lambda c: c["start"])
    kept: list[dict] = []
    for ch in found:
        if kept and ch["start"] - kept[-1]["start"] < MIN_GAP_SECONDS:
            continue
        kept.append(ch)
    if kept:
        kept[0]["start"] = 0.0
    return kept[:MAX_CHAPTERS]


def timestamp_list(chapters: list[dict]) -> str:
    """The YouTube description form: one 'm:ss Title' per line, starting at 0:00."""
    return "\n".join(f"{format_clock(c['start'])} {c['title']}" for c in chapters) + "\n"
