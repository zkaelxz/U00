"""Sorts an error text into one of a few fixed kinds, so a log can count errors without quoting them."""

import re

KINDS = ("429", "overload", "timeout", "blank", "parse", "other")

# Order matters: "429 Too Many Requests ... overloaded" counts once, as 429.
_PATTERNS = (
    ("429", re.compile(r"(?<!\d)429(?!\d)|rate.?limit|too many requests", re.I)),
    ("overload", re.compile(r"overload|(?<!\d)(?:503|529)(?!\d)|service unavailable|capacity", re.I)),
    ("timeout", re.compile(r"time(?:d)?[ -]?out|timed out", re.I)),
    ("blank", re.compile(r"no translation|left (?:them )?out|empty|blank", re.I)),
    ("parse", re.compile(r"parse|json|malformed|reply check|cut off|invalid", re.I)),
)


def error_kind(text) -> str:
    text = str(text or "")
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return "other"
