"""Line text exchanged with a model: the spoken-language tags and numbered-line block a
prompt shows, and cleaning and parsing what the reply sends back."""

import json
import re

from core import LANGUAGE_NAMES


def parse_json_array(text: str, fallback_count: int):
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        arr = json.loads(text)
        if isinstance(arr, list):
            return arr
    except json.JSONDecodeError:
        pass
    # fallback: try to split numbered lines
    lines = [l.split(". ", 1)[-1].strip() for l in text.splitlines() if l.strip()]
    return lines[:fallback_count] if lines else [""] * fallback_count


def language_name(lang, default: str | None = None) -> str:
    """Display name of a line language code; LANGUAGE_NAMES has no English
    because a title's own language never is."""
    return LANGUAGE_NAMES.get(lang, "English" if lang == "en" else default or lang)


def spoken_language_tag(lang) -> str:
    """The prompt prefix for a line spoken in a language other than the
    title's ("" for a line in the title's language, which is untagged so
    a single-language title's prompt is unchanged)."""
    if not lang:
        return ""
    return f"(spoken in {language_name(lang)}) "


def tagged_line_languages(lines, title_language: str):
    """context["line_languages"] for these lines: each line's lang where it
    differs from the title's, None elsewhere; None overall when no line
    differs so a single-language title sends the prompt it always did."""
    langs = [getattr(ln, "lang", None) for ln in lines]
    langs = [lang if lang and lang != title_language else None for lang in langs]
    return langs if any(langs) else None


def tagged_source_texts(lines, title_language: str) -> list:
    """Each line's source text as the model should read it: prefixed with
    the spoken-language tag where that differs from the title's."""
    langs = tagged_line_languages(lines, title_language) or [None] * len(lines)
    return [spoken_language_tag(lang) + ln.zh for lang, ln in zip(langs, lines)]


def is_english_line(line) -> bool:
    """Already English, so translating it would only spend a model call."""
    return getattr(line, "lang", None) == "en"


def strip_echoed_label(speaker, reply: str) -> str:
    """The reply without a leading "[Name]" copied from the numbered block, where
    Name is this line's own speaker label. Any other bracket is the model's text
    (a stage direction, a test marker) and stays."""
    if not reply or not speaker:
        return reply
    match = re.match(r"^\s*\[([^\[\]\n]{1,40})\]\s*", reply)
    if not match or match.group(1).strip().casefold() != str(speaker).strip().casefold():
        return reply
    stripped = reply[match.end():]
    return stripped if stripped.strip() else reply


# translategemma:12b has answered one line with "Strange. Or: Weird. Or: Odd.": a list of
# alternatives where a subtitle takes exactly one. "Or:" opening a sentence is the marker; a
# plain "or" inside a sentence is ordinary dialogue and never matches.
_ALTERNATIVE_MARKER = re.compile(r"(?<=[.!?…])\s+(?:Or|Alternatively):\s")


def keep_first_alternative(reply: str) -> str:
    """The reply cut to its first answer when the model listed several
    ("Strange. Or: Weird." -> "Strange."). Unchanged when there is no marker
    or nothing would be left before it."""
    if not reply:
        return reply
    match = _ALTERNATIVE_MARKER.search(reply)
    if not match:
        return reply
    first = reply[:match.start()].strip()
    return first or reply


def build_numbered_lines(ids: list, zh_lines: list, speaker_names: list | None = None,
                         languages: list | None = None) -> str:
    """
    Builds the numbered-line block shown to the model, e.g.:
        1. [Xiaoling] 你好
        2. 那天下着雨。
    speaker_names (parallel to zh_lines, None entries allowed) prefixes
    only the lines a name is actually known for -- a line with no known
    speaker (narration, an unlabeled line) is left unprefixed rather
    than showing a placeholder like "[Unknown]", which would just be
    noise the model has to ignore.
    languages (parallel, None entries allowed) tags only lines spoken in a
    language other than the title's, e.g. "3. (spoken in Korean) 안녕".
    """
    out = []
    for idx, (i, zh) in enumerate(zip(ids, zh_lines)):
        name = speaker_names[idx] if speaker_names else None
        prefix = f"[{name}] " if name else ""
        tag = spoken_language_tag(languages[idx]) if languages else ""
        # A break inside a source line would start a row that reads as another line's number.
        out.append(f"{i}. {prefix}{tag}{' '.join(part.strip() for part in zh.splitlines() if part.strip())}")
    return "\n".join(out)


def extract_first_json_value(text: str, expect: type | None = None):
    """Finds and parses the first valid JSON object/array anywhere in
    text, tolerating surrounding prose ("Here you go:\n{...}\nHope that
    helps!") -- small local models wrap their JSON in commentary like
    this often. Uses the real JSON decoder to find the end of the
    value (via raw_decode), rather than a regex guessing at matching
    brackets, so a brace/bracket character inside a string value can't
    make it stop early or grab too much. expect (dict or list): skip
    values of the other type, so prose like "Line [3] looks off" before
    the answer object isn't taken for it. Returns None if nothing in
    the text parses as JSON (of that type)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            if expect is None or isinstance(value, expect):
                return value
    return None


def parse_line_id(value):
    """2, 2.0, "2", " 2 " and "[2]" (the prompt's own format) all give 2; else None."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    m = re.fullmatch(r"\s*\[?\s*(-?\d+)\s*\]?\s*", value) if isinstance(value, str) else None
    return int(m.group(1)) if m else None


def parse_flag_entries(entries):
    """({line id: entry}, count of entries whose line_idx could not be read)."""
    by_id, unreadable = {}, 0
    for f in entries if isinstance(entries, list) else []:
        fid = parse_line_id(f.get("line_idx") if isinstance(f, dict) else None)
        if fid is None:
            unreadable += 1
        else:
            by_id[fid] = f
    return by_id, unreadable
