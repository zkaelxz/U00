"""Sorts an error text into one of a few fixed kinds, so a log can count errors without quoting them."""

import re

KINDS = ("429", "overload", "timeout", "blank", "parse", "other")

# Order matters: "429 Too Many Requests ... overloaded" counts once, as 429. Phrases are
# anchored on how the app's engines word these failures, so an unrelated message that
# merely contains "invalid" or "empty" falls to "other" rather than into a wrong bucket.
_PATTERNS = (
    ("429", re.compile(r"^\s*429\b|(?:http|status|error|code)[ :]*429\b|\b429 (?:too|client)|rate[ -]limit|too many requests", re.I)),
    ("overload", re.compile(r"\boverloaded\b|\b(?:503|529)\b|service unavailable", re.I)),
    ("timeout", re.compile(r"\btimed? ?out\b|\btimeout\b", re.I)),
    ("blank", re.compile(r"got no translation|left them out|empty (?:reply|response)", re.I)),
    ("parse", re.compile(r"reply check|invalid json|not valid json|could not (?:be )?pars|malformed|cut off", re.I)),
)


def error_kind(text) -> str:
    text = str(text or "")
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return "other"
