"""
services/hardsub_speaker_compare.py -- checks a transcript against a per-speaker hard-sub reference: how
much of each speaker group's burned-in text the transcript contains, and how well the transcript's speaker
labels line up with the groups. Pure; the reference comes from hardsub_speaker_read_service.
"""
from collections import Counter

from services.benchmark_scoring_service import _to_simplified, normalise_text, script_normaliser


def _chars(text: str, normaliser: str) -> Counter:
    return Counter(normalise_text(_to_simplified(text, normaliser), "char"))


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _spans(reference_lines: list) -> list:
    """The reference lines' time ranges merged where they overlap, as [[start, end, [lines]]]. Several
    speakers talk over one stretch of audio and the transcript puts their words in one order or another,
    so characters are compared per stretch, never line to line."""
    spans = []
    for line in sorted(reference_lines, key=lambda r: r["start"]):
        if spans and line["start"] < spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], line["end"])
            spans[-1][2].append(line)
        else:
            spans.append([line["start"], line["end"], [line]])
    return spans


def compare(groups: list, lines: list, language: str = "zh") -> dict:
    """`groups` is [{"key", "lines": [{"start", "end", "text"}]}]; `lines` the transcript's rows (db.load_lines).
    Returns per-group recall (characters of the group's text found in the transcript over the same time
    span, in any order), overall recall and precision, and the speaker-label overlap matrix with the share
    of overlapping time where a label's busiest group is the group it maps to."""
    normaliser = script_normaliser("ocr", language)
    tagged = [dict(line, group=g["key"]) for g in groups for line in g["lines"]]
    found, total = Counter(), Counter()
    matched_all = ref_all = heard_all = 0
    for start, end, span_lines in _spans(tagged):
        heard = _chars(" ".join(ln["zh"] for ln in lines if _overlap(start, end, ln["start"], ln["end"]) > 0), normaliser)
        wanted = Counter()
        for ref in span_lines:
            chars = _chars(ref["text"], normaliser)
            wanted += chars
            total[ref["group"]] += sum(chars.values())
            found[ref["group"]] += sum((chars & heard).values())
        matched_all += sum((wanted & heard).values())
        ref_all += sum(wanted.values())
        heard_all += sum(heard.values())
    matrix = {}
    for ln in lines:
        if not ln.get("speaker"):
            continue
        for ref in tagged:
            seconds = _overlap(ref["start"], ref["end"], ln["start"], ln["end"])
            if seconds > 0:
                row = matrix.setdefault(ln["speaker"], {})
                row[ref["group"]] = round(row.get(ref["group"], 0.0) + seconds, 3)
    overlap = sum(sum(row.values()) for row in matrix.values())
    best = sum(max(row.values()) for row in matrix.values())
    return {
        "groups": [{"key": g["key"], "characters": total[g["key"]],
                    "recall": round(found[g["key"]] / total[g["key"]], 4) if total[g["key"]] else None}
                   for g in groups],
        "recall": round(matched_all / ref_all, 4) if ref_all else None,
        "precision": round(matched_all / heard_all, 4) if heard_all else None,
        "label_overlap": matrix,
        "label_match": round(best / overlap, 4) if overlap else None,
    }
