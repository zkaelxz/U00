"""
subtitle_sidecar.py -- what language a subtitle text is in.
"""

import re
from typing import Optional

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
