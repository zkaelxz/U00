"""Parsing an id-keyed JSON reply ({"12": "text"}) into {id: text}, including
the complete pairs of a reply cut off by the output limit, and a fenced JSON
reply taken whole. No provider code."""

import json
import re


def load_json_reply(text: str, default):
    """Parses a model reply that may be wrapped in a markdown fence. Only the
    reply as a whole is parsed: prose around the JSON returns `default`
    (extract_first_json_value is for replies that may carry prose)."""
    stripped = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return default


def parse_id_keyed_json(text: str, expected_ids: list, recover: bool = False,
                        truncated: bool = False) -> dict:
    """
    Parses a response expected to be a JSON object mapping each line's
    id (as a string) to its translation, e.g. {"1": "Hello.", "2": "Hi."}.
    Returns {id_str: text} for whichever of expected_ids actually came
    back with an actual string translation -- a missing id (or one
    whose value isn't a string, e.g. null or a nested list) just isn't
    a key here, it's the caller's job (request_translations_with_retry)
    to decide what to do about that, not this function's.

    A plain JSON array (a model ignoring the object-shape instruction) is
    malformed and returns {}: an array can be short, long or reordered,
    so matching it to ids by position could put a translation on the
    wrong line. The retry path re-requests those ids instead.

    recover (for local models, see _recover_id_keyed): take the last object
    rather than the first, and keep the complete pairs of a cut-off reply.

    truncated: the provider itself said the reply was cut off at its output
    limit (shared.TruncatedReply). Only then are the finished pairs of an
    incomplete object kept; any other malformed reply is {} and is asked for
    again, because a string with a stray quote in it would otherwise be kept
    as a half translation.
    """
    stripped = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    expected_str = {str(i) for i in expected_ids}
    if recover:
        return _recover_id_keyed(stripped, expected_str)
    # An object that fails to parse in a reply known to be cut off is the cut
    # one: its inside is not searched, or a nested echo would be read as top level.
    found = _first_id_object(stripped, expected_str, descend=not truncated)
    # A reply cut off by the output limit has no complete object; its finished
    # "id": "text" pairs are still good, matched to their line by id.
    if found or not truncated:
        return found
    return _salvage_top_level_pairs(stripped, expected_str)


def _id_pairs(data, expected_str: set) -> dict:
    return {str(k): v for k, v in data.items() if str(k) in expected_str and isinstance(v, str)}


def _first_id_object(text: str, expected_str: set, descend: bool = True) -> dict:
    """The first JSON object in text that holds any expected id, so prose such
    as "Lines [1] and [2]:" before the object is skipped. A single-key wrapper
    ({"translations": {...}}) is opened."""
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        if text[i] not in "{[":
            i += 1
            continue
        try:
            value, stop = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            if text[i] == "{" and not descend:
                break
            i += 1
            continue
        if isinstance(value, dict):
            if len(value) == 1:
                inner = next(iter(value.values()))
                if isinstance(inner, dict):
                    value = inner
            found = _id_pairs(value, expected_str)
            if found:
                return found
        i = stop
    return {}


def _salvage_top_level_pairs(text: str, expected_str: set) -> dict:
    """The finished "id": "text" pairs of the top-level object in a cut-off
    reply. Reads the object entry by entry and stops at the first thing that
    is not `,` or `}` after a value, so a string holding an unescaped quote
    is never kept as a shorter one, and a pair is kept only once the `,` that
    follows it proves it was written to the end. Pairs nested in another
    value (a "source" echo beside the translations) are never read, and the
    first value of a repeated id wins."""
    decoder = json.JSONDecoder()
    opening = re.search(r"\{\s*\"", text)
    if opening is None:
        return {}
    found, i = {}, opening.start() + 1
    while True:
        i = _skip_space(text, i)
        try:
            key, i = decoder.raw_decode(text, i)
            i = _skip_space(text, i)
            if text[i] != ":":
                break
            i = _skip_space(text, i + 1)
            value, i = decoder.raw_decode(text, i)
        except (json.JSONDecodeError, IndexError):
            break
        i = _skip_space(text, i)
        if i >= len(text) or text[i] not in ",}":
            break
        if isinstance(key, str) and key in expected_str and isinstance(value, str):
            found.setdefault(key, value)
        if text[i] == "}":
            break
        i += 1
    return found


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


# One complete "id": "text" pair (local models, see _recover_id_keyed); the value's closing quote proves it was
# written to the end, unlike the pair a reply was cut off in.
_ID_PAIR_RE = re.compile(r'"\s*(\d+)\s*"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _salvage_pairs(text: str, expected_str: set) -> dict:
    found = {}
    for m in _ID_PAIR_RE.finditer(text):
        if m.group(1) not in expected_str:
            continue
        try:
            found[m.group(1)] = json.loads(f'"{m.group(2)}"')
        except json.JSONDecodeError:
            continue
    return found


def _recover_id_keyed(text: str, expected_str: set) -> dict:
    """A weak model may write a draft object before its answer, or run out of
    output mid-object. The answer is what it wrote last: a cut-off object
    after the last complete one gives its complete pairs, else the last
    complete object holding any expected id is used."""
    decoder = json.JSONDecoder()
    objects, i, end = [], 0, 0
    while i < len(text):
        if text[i] == "{":
            try:
                value, stop = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                i += 1
                continue
            if isinstance(value, dict):
                objects.append(value)
                end = stop
            i = stop
            continue
        i += 1
    tail = text[end:]
    if "{" in tail:
        salvaged = _salvage_pairs(tail[tail.index("{"):], expected_str)
        if salvaged:
            return salvaged
    for data in reversed(objects):
        found = {str(k): v for k, v in data.items() if str(k) in expected_str and isinstance(v, str)}
        if found:
            return found
    return {}
