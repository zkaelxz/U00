"""
scanlate_watermark.py -- spots a source site's watermark among a page's text regions.

A reader site stamps its name or address on every page. OCR reads it like any
other lettering, so without this it is translated and typeset onto the art.
"""

import difflib
import re
import unicodedata
from collections.abc import Sequence

_DOMAIN = re.compile(
    r"(https?://|www\.|\b[a-z0-9][a-z0-9-]{1,}\.(?:com|net|org|co|kr|cc|me|tv|xyz|top|site|"
    r"info|io|jp|cn|tw|to|app|vip|club|moe)\b)", re.I)

# Scanlators' credit lines come out of OCR as one Latin run with a dot inside ("Feaserasa.cripmon"),
# which no list of domains covers. Two runs of four letters or more joined by a dot with no space
# is an address, not dialogue (a sentence break has a space after its dot, and OCR that drops it
# leaves a capital after the dot, so the right-hand run must be lowercase).
_LATIN_ADDRESS = re.compile(r"[A-Za-z]{4,}\.[a-z]{4,}")

# An address stamped under a Korean or CJK site name has no Latin label before
# the dot, and OCR garbles the name itself ("토끼뉴소.com"), so the dot-and-suffix
# after any CJK character is enough to call it an address.
_CJK_DOMAIN = re.compile(
    r"[\u3040-\u9fff\uac00-\ud7af]\.(?:com|net|org|co|kr|cc|me|tv|xyz|top|site|"
    r"info|io|jp|cn|tw|to|app|vip|club|moe)\b", re.I)

# Names of reader sites whose stamp has no address in it. OCR drops the dots
# of a stamped address often enough that the bare name has to match as well.
_SITE_NAMES = ("marumaru", "마루마루", "newtoki", "뉴토끼", "newtojuso", "뉴토주소", "toonkor", "툰코",
               "copymanga",
               "newtokki", "webtoonkingdom", "fastestwebtoon", "웹툰왕국", "빠른웹툰", "빠른웹튼", "웹툰공급",
               "拷贝漫画", "拷貝漫畫", "mangadex", "twmanga", "kuaikan", "快看漫画", "快看漫畫")

# Reader-site ads read as dialogue once translated. "Fastest webtoon" and
# "search on Google" are the ad's own phrases, rare in a story. OCR of a small
# stamp over art often keeps only the ends of the first line ("가장른 미리보가"),
# so "미리보" counts when "가장" or "웹툰" sits just before it, and "구글" still leads the
# site's name when 검색 between them is misread ("구글감때뉴토" was lettered as a name).
_PROMO = re.compile(r"가장\s*빠른\s*웹[툰튼]|구글\s*검색|(?:가장|웹[툰튼]).{0,4}미[리\d]{1,3}보"
                    r"|구글.{0,3}뉴토")

# A stamp line by itself: the site's name cut short, or "미리보기" (preview)
# with a digit read in for a stroke ("미12보7"). Matched only against a whole
# line, since these are ordinary syllables inside a sentence.
_STAMP_LINE = re.compile(r"^(?:뉴토끼?|미[리\d]{1,3}보[기\d]?)$")

# OCR reads the one 뉴토끼 stamp many ways ("뉴로끼", "구을검식뉴토끼", "기빠문 미리보기"),
# so a whole line close enough to one of its parts counts. Each form is compared only with
# lines of nearly its own length. Ordinary words share syllables with the stamp (토끼 is
# "rabbit", 미리 보기 is "preview"), so two kinds of near match are left out: a line that is
# only a few of the form's own syllables with nothing misread, perhaps with an ending after
# them ("미리 보기", "웹툰 보기가"), and
# a three-syllable line that has lost the name's rare first syllable ("토끼야!", "산토끼").
_STAMP_FORMS = ("구글검색뉴토끼", "구글검색", "뉴토끼", "빠른웹툰미리보기", "가장빠른웹툰미리보기", "웹툰미리보기")
_STAMP_SIMILARITY = 0.66
_STAMP_MIN_CHARS = 3
_STAMP_LENGTH_SLACK = 2
# A run of the ad's own syllables this short can be a real phrase; a longer one is the ad.
_MAX_WORDLIKE_FRAGMENT = 4
_SHORT_NAME = "뉴토끼"
_HANGUL_FIRST, _HANGUL_VOWEL_SPAN, _HANGUL_FINAL_SPAN = 0xAC00, 588, 28
_INITIAL_N = 2
_VOWEL_U, _VOWEL_YU, _VOWEL_UI = 13, 17, 19


def _jamo(syllable: str):
    """(initial, vowel, final) indexes of a Hangul syllable, or None."""
    code = ord(syllable) - _HANGUL_FIRST
    if not 0 <= code < 19 * _HANGUL_VOWEL_SPAN:
        return None
    return code // _HANGUL_VOWEL_SPAN, code % _HANGUL_VOWEL_SPAN // _HANGUL_FINAL_SPAN, code % _HANGUL_FINAL_SPAN


def _misread_short_name(text: str) -> bool:
    """A three-syllable line is the bare site name only when its first syllable still looks
    like 뉴: the vowel ㅠ kept (뮤, 듀), or ㄴ with a vowel a stroke away and no final (누, 늬).
    "뉴로끼" and "누토끼" are misreads; "산토끼" (hare), "두 토끼" (two rabbits), "네 토끼"
    (your rabbit) and "토끼야" are words."""
    if len(text) != len(_SHORT_NAME) or sum(a != b for a, b in zip(text, _SHORT_NAME)) != 1:
        return False
    if text[0] == _SHORT_NAME[0]:
        return True
    jamo = _jamo(text[0])
    if jamo is None:
        return False
    initial, vowel, final = jamo
    return vowel == _VOWEL_YU or (initial == _INITIAL_N and final == 0 and vowel in (_VOWEL_U, _VOWEL_UI))


def _close_to_form(text: str, form: str) -> bool:
    if abs(len(form) - len(text)) > _STAMP_LENGTH_SLACK:
        return False
    matcher = difflib.SequenceMatcher(None, text, form)
    if matcher.ratio() < _STAMP_SIMILARITY:
        return False
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    kept = sum(block.size for block in blocks)
    # Nothing misread: every syllable before the last matched one is the form's own, and
    # what follows is an ending ("미리보기는", "웹툰 보기 싫어").
    unmisread = bool(blocks) and kept == blocks[-1].a + blocks[-1].size
    return not (unmisread and kept <= _MAX_WORDLIKE_FRAGMENT)


def _resembles_stamp(line: str) -> bool:
    text = _normal(line)
    if len(text) < _STAMP_MIN_CHARS:
        return False
    if text in _STAMP_FORMS:
        return True
    return any(_misread_short_name(text) if form == _SHORT_NAME and len(text) == len(form)
               else _close_to_form(text, form) for form in _STAMP_FORMS)


# A repeat counts as one stamp only while it stays in the same spot. The
# stamp is a fixed overlay, whereas a line of dialogue that happens to repeat
# (a name, a sigh) moves with its balloon.
_SAME_SPOT = 0.08            # of the page's width / height, centre to centre
_EDGE_BAND = 0.18            # of the page's width / height from each edge
_MIN_REPEAT_CHARS = 3
# What must be left of a region once its stamp lines are dropped for it to be
# lettering that happens to share a box with the stamp, not the stamp itself.
_MIN_LETTERING_CHARS = 6

_STRIP = re.compile(r"[\W_]+", re.UNICODE)


def _normal(text: str | None) -> str:
    return _STRIP.sub("", (text or "")).lower()


def looks_like_site_mark(text: str) -> bool:
    """True when the text is a web address or a known reader site's name."""
    text = text or ""
    if _DOMAIN.search(text) or _CJK_DOMAIN.search(text) or _PROMO.search(_STRIP.sub("", text)):
        return True
    if _LATIN_ADDRESS.search(text):
        return True
    squashed = _normal(text)
    return any(name in squashed for name in _SITE_NAMES)


def _is_stamp_line(line: str) -> bool:
    return looks_like_site_mark(line) or bool(_STAMP_LINE.match(_normal(line))) or _resembles_stamp(line)


def _without_mark_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _is_stamp_line(line))


def _centre(region: dict, width: float, height: float):
    return ((region["x"] + region["w"] / 2) / width, (region["y"] + region["h"] / 2) / height)


def _on_edge(centre) -> bool:
    return any(c < _EDGE_BAND or c > 1 - _EDGE_BAND for c in centre)


def _same_stamp(region: dict, centre, others: Sequence[dict], width: float, height: float) -> bool:
    text = _normal(region.get("source_text"))
    for other in others:
        if _normal(other.get("source_text")) != text:
            continue
        ox, oy = _centre(other, width, height)
        if abs(ox - centre[0]) <= _SAME_SPOT and abs(oy - centre[1]) <= _SAME_SPOT:
            return True
    return False


def flag_site_marks(regions: list, earlier_pages: Sequence[list] = (), width: int = 0, height: int = 0) -> int:
    """Sets skip on every region that is a site watermark, so it is neither
    translated nor painted over. A region is one when its text is an address or
    a known site name, or when it sits in the page's edge band with the same
    text and place as a region on one of `earlier_pages` (each a list of
    regions from a page of the same size). A region that also holds real lettering
    keeps it: only the stamp's lines are cut from its text. Returns how many
    were flagged."""
    flagged = 0
    for region in regions:
        if region.get("skip") or not (region.get("source_text") or "").strip():
            continue
        mark = looks_like_site_mark(region["source_text"]) or _resembles_stamp(region["source_text"])
        lines = region["source_text"].splitlines()
        if not mark and len(lines) > 1 and any(
                _STAMP_LINE.match(_normal(ln)) or _resembles_stamp(ln) for ln in lines):
            # A cut-short name only counts beside other lines: alone it could be a word.
            mark = True
        if mark:
            # A caption box near the stamp can take its lines into one region.
            # Skipping that region would drop the caption with the stamp, so
            # only the stamp's own lines are removed from its text.
            lettering = _without_mark_lines(region["source_text"])
            if len(_normal(lettering)) >= _MIN_LETTERING_CHARS:
                region["source_text"] = lettering
                mark = False
        if (not mark and width > 0 and height > 0
                and len(_normal(region["source_text"])) >= _MIN_REPEAT_CHARS):
            centre = _centre(region, width, height)
            mark = _on_edge(centre) and any(
                _same_stamp(region, centre, earlier, width, height) for earlier in earlier_pages)
        if mark:
            region["skip"] = True
            flagged += 1
    return flagged


_FRAGMENT_MAX_WIDTH = 0.06    # of the page's width
_FRAGMENT_MAX_HEIGHT = 0.04   # of the page's height
_FRAGMENT_EDGE_PX = 3


def flag_edge_fragments(regions: list, width: int, height: int) -> int:
    """Sets skip on a tiny region that touches the page's edge. The detector
    boxes scan-edge debris and a clipped corner of a stamp, and OCR then reads
    it as a word ("Miyamura Village" from a few pixels of a corner), which
    would be translated and typeset onto the art. Real lettering is never
    this small and flush against the edge. Returns how many were flagged."""
    if width <= 0 or height <= 0:
        return 0
    flagged = 0
    for region in regions:
        if region.get("skip"):
            continue
        x, y, w, h = (int(region.get(k) or 0) for k in ("x", "y", "w", "h"))
        if w > width * _FRAGMENT_MAX_WIDTH or h > height * _FRAGMENT_MAX_HEIGHT:
            continue
        if min(x, y, width - (x + w), height - (y + h)) <= _FRAGMENT_EDGE_PX:
            region["skip"] = True
            flagged += 1
    return flagged


_NUMBER_MAX_WIDTH = 0.05      # of the page's width
_NUMBER_MAX_HEIGHT = 0.03     # of the page's height
_NUMBER_BAND = 0.08           # of the page's height, from the top or bottom edge
# A webtoon strip is far taller than wide, so the height fractions alone would
# open a margin thousands of pixels deep: both are also capped by the width.
_NUMBER_MAX_HEIGHT_PER_WIDTH = 0.06
_NUMBER_BAND_PER_WIDTH = 0.12
_NUMBER_MAX_CHARS = 3


def flag_already_english(regions: list, source_language: str) -> int:
    """Skips regions already in Latin script on a CJK page (an English logo or
    credit on a cover): redrawing them would replace the artist's own English
    lettering with a worse copy."""
    if source_language not in ("zh", "ja", "ko"):
        return 0
    flagged = 0
    for r in regions:
        # A bubble's English is dialogue the person may have kept or un-skipped on purpose; only
        # lettering outside a balloon (logo, credit, caption, sign) is the artist's own English.
        if r.get("skip") or r.get("kind", "bubble") == "bubble":
            continue
        # OCR reads an English sign in fullwidth letters on CJK pages ("ＡＤＶＥＮＴ…").
        letters = [c for c in unicodedata.normalize("NFKC", r.get("source_text") or "") if c.isalpha()]
        latin = sum(1 for c in letters if c.isascii())
        if latin >= 3 and latin >= 0.8 * len(letters):
            r["skip"] = True
            flagged += 1
    return flagged


def flag_page_numbers(regions: list, width: int, height: int) -> int:
    """Sets skip on a page number: a few characters in a tiny box inside the
    top or bottom margin. An italic "88" in a corner was read as the sound
    "おお" and typeset as "Wow", and OCR does the same with other numerals. A
    region this small and this short in the margin is never dialogue, because
    balloons sit inside the panels. Returns how many were flagged."""
    if width <= 0 or height <= 0:
        return 0
    max_h = min(height * _NUMBER_MAX_HEIGHT, width * _NUMBER_MAX_HEIGHT_PER_WIDTH)
    band = min(height * _NUMBER_BAND, width * _NUMBER_BAND_PER_WIDTH)
    flagged = 0
    for region in regions:
        if region.get("skip"):
            continue
        text = _normal(region.get("source_text"))
        if not text or len(text) > _NUMBER_MAX_CHARS:
            continue
        x, y, w, h = (int(region.get(k) or 0) for k in ("x", "y", "w", "h"))
        if w > width * _NUMBER_MAX_WIDTH or h > max_h:
            continue
        centre_y = y + h / 2
        if centre_y < band or centre_y > height - band:
            region["skip"] = True
            flagged += 1
    return flagged


def is_site_mark_region(region: dict) -> bool:
    """A skipped region whose text is a site's stamp: the one skipped kind the
    render paints out, because the stamp is not part of the art."""
    return bool(region.get("skip")) and looks_like_site_mark(region.get("source_text") or "")


# The OCR box of a stamp holds only its address strip; the logo is a square
# about as wide as that strip, sitting above it.
_LOGO_SIDE_PER_STRIP_WIDTH = 1.15
_LOGO_MARGIN_PX = 8


def logo_box(region: dict, page_width: int, page_height: int) -> dict:
    """The area a stamp covers, grown from its OCR box: wider by a margin and
    reaching upward by the logo's side, clamped to the page."""
    side = int(region["w"] * _LOGO_SIDE_PER_STRIP_WIDTH) + 2 * _LOGO_MARGIN_PX
    left = max(0, region["x"] - _LOGO_MARGIN_PX)
    right = min(page_width, region["x"] + region["w"] + _LOGO_MARGIN_PX)
    bottom = min(page_height, region["y"] + region["h"] + _LOGO_MARGIN_PX)
    top = max(0, bottom - max(side, region["h"] + 2 * _LOGO_MARGIN_PX))
    if _lacks_site_name_line(region):
        # The site-name line and the logo OCR never reads lie under and around
        # the one line it did read.
        left = max(0, region["x"] - int(2.5 * region["h"]))
        right = min(page_width, region["x"] + region["w"] + region["h"])
        bottom = min(page_height, bottom + 3 * region["h"])
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def _lacks_site_name_line(region: dict) -> bool:
    """True for a read of the promo's first line alone: the stamp has a second
    line, with the site's name, under it that OCR of the faint overlay does not find."""
    text = region.get("source_text") or ""
    return "\n" not in text.strip() and bool(_PROMO.search(_STRIP.sub("", text))) and "구글" not in text
