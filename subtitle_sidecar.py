"""
subtitle_sidecar.py -- which subtitle file belongs to a media file, and what
language a subtitle text is in.

Works on file *names* and text only: it never reads a folder or a path, so the
API can rank the names a browser picked and the CLI can rank a directory
listing with the same code. Several matches are all returned, best first; the
caller shows them rather than silently binding one.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

SUBTITLE_EXTENSIONS = ("srt", "vtt", "ass", "ssa", "lrc")
_EXT_ORDER = {ext: i for i, ext in enumerate(SUBTITLE_EXTENSIONS)}

# Language tokens seen in subtitle file names, mapped to the app's codes.
# Unlisted tokens still match as "a language token", just with no known code.
_TOKEN_CODES = {
    "zh": "zh", "chs": "zh", "cht": "zh", "chi": "zh", "zho": "zh", "cn": "zh", "sc": "zh", "tc": "zh",
    "zh-cn": "zh", "zh-tw": "zh", "zh-hans": "zh", "zh-hant": "zh", "zh-hk": "zh",
    "ja": "ja", "jp": "ja", "jpn": "ja", "jap": "ja", "ko": "ko", "kr": "ko", "kor": "ko",
    "en": "en", "eng": "en", "en-us": "en", "en-gb": "en",
}
_LANG_TOKEN = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,4})?")


@dataclass
class SidecarCandidate:
    name: str
    format: str
    language_token: Optional[str]  # the token in the name, as written; None when absent
    language: Optional[str]        # its app code, None when unknown or absent
    exact: bool                    # no language token: <name>.<ext> or <name>.<media ext>.<ext>


def _norm(value: str) -> str:
    # Full-width letters and digits are common in file names from Japan.
    return unicodedata.normalize("NFKC", value).casefold()


def _split_ext(name: str):
    stem, dot, ext = name.rpartition(".")
    return (stem, ext.lower()) if dot and stem else (name, "")


def rank_sidecars(media_name: str, names: list, prefer_language: Optional[str] = None) -> list:
    """Candidates among `names` for the media file `media_name`, best first.
    Accepted: <name>.<ext>, <name>.<lang>.<ext>, <name>.<media ext>.<ext> and
    <name>.<media ext>.<lang>.<ext>. Ranked by language (the title's
    `prefer_language` first, unknown next, other languages last), then
    names without a language token, then format, then name."""
    media_stem, media_ext = _split_ext(media_name)
    stem_norm, full_norm = _norm(media_stem), _norm(media_name)
    out = []
    for name in names:
        base, ext = _split_ext(name)
        if ext not in SUBTITLE_EXTENSIONS:
            continue
        base_norm = _norm(base)
        token = None
        for prefix in (full_norm, stem_norm):
            if base_norm == prefix:
                break
            if base_norm.startswith(prefix + "."):
                tail = base_norm[len(prefix) + 1:]
                if _LANG_TOKEN.fullmatch(tail):
                    token = tail
                    break
        else:
            continue
        out.append(SidecarCandidate(name, "ass" if ext == "ssa" else ext, token,
                                    _TOKEN_CODES.get(token) if token else None, token is None))

    def key(c):
        if prefer_language and c.language == prefer_language:
            lang_rank = 0
        elif c.language is None:
            lang_rank = 1
        else:
            lang_rank = 2
        return (lang_rank, 0 if c.exact else 1, _EXT_ORDER.get(c.format, 9), c.name)
    return sorted(out, key=key)


_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def detect_language(text: str) -> Optional[str]:
    """"ja", "ko", "zh", "latin" or None, from which scripts the text uses.
    Kana mean Japanese even when Han characters outnumber them; Han text with
    almost no kana or Hangul is Chinese. Latin can't tell English from
    French, so it stays "latin"."""
    han, kana = len(_HAN.findall(text)), len(_KANA.findall(text))
    hangul, latin = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    cjk = han + kana + hangul
    if cjk == 0:
        return "latin" if latin else None
    if latin > cjk * 3:
        return "latin"
    if hangul > han + kana:
        return "ko"
    if kana >= max(1, cjk * 0.03):
        return "ja"
    return "zh"
