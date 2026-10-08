"""
sources/charset_sniff.py -- decoding a page that declares no charset.

UTF-8 if it is valid, otherwise whichever legacy CJK encoding makes the best-
scoring text; split out of sources/http.py to keep that module from growing.
"""

# Encodings tried, in order, for a page that declares no charset and is not
# valid UTF-8. Order is the tie-break when two score the same.
_SNIFF_CANDIDATES = ("gb18030", "big5", "cp932", "euc_jp", "euc_kr")
_SNIFF_SAMPLE = 64 * 1024


# Very common ideographs (simplified, traditional and shared kanji forms). A
# wrong legacy decode yields ideographs too, but rarely these ones.
_COMMON_HANZI = frozenset(
    "的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后小么心多天而能好都然没日于起还发成事只作当想看文无开手十用主行方又如前所本见经头面公同三已老从动两长知民样现分将外但身些与高意进把法此实回二理美点月明其种声全工话儿者向情部正名定女问力机给等几很业最间新什写活加相"
    "的一是不了人我在有他這中大來上國個到說們為子和你地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多天而能好都然沒日於起還發成事只作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動兩長知民樣現分將外但身些與高意進把法此實回二理美點月明其種聲全工話兒者向情部正名定女問力機給等幾很業最間新什寫活加相"
    "第章節話卷後彼私俺僕君達的様事時間何者人日本語今見行来出会思言生学校手気分中女男子大小上下前後年月"
)


def _sniff_score(text: str, kana_counts: bool = True, hanja_weight: float = 0.2) -> float:
    """How much `text` looks like real CJK prose. Kana and hangul are strong
    evidence, common ideographs are good evidence, other ideographs weak;
    replacement, control, private-use, rare (extension / compatibility)
    ideographs (in Korean, any non-common one) and half-width katakana (what EUC-JP looks like when misread
    as Shift-JIS) count heavily against."""
    score = 0.0
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:
            # GB2312 has kana in the same cells as EUC-JP, so they say nothing
            # when the candidate is GB18030.
            score += 1.5 if kana_counts else 0
        elif 0xAC00 <= o <= 0xD7A3:
            score += 1
        elif ch in _COMMON_HANZI:
            score += 2
        elif 0x4E00 <= o <= 0x9FFF:
            score += hanja_weight
        elif o == 0xFFFD or o < 0x20 and ch not in "\t\n\r" or 0x7F <= o < 0xA0 \
                or 0xE000 <= o <= 0xF8FF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF \
                or 0xFF61 <= o <= 0xFF9F:
            score -= 10
    return score


def sniff_decode(content: bytes) -> str:
    """Decodes bytes with no declared charset: BOM, then strict UTF-8, then
    the best-scoring legacy CJK encoding on a bounded sample. Never raises."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if content.startswith(bom):
            return content.decode(enc, errors="replace")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    sample = content[:_SNIFF_SAMPLE]
    best, best_score = None, 0.0
    for enc in _SNIFF_CANDIDATES:
        text = None
        # The sample may cut a multi-byte character in half.
        for trim in range(4):
            try:
                text = (sample[:len(sample) - trim] if trim else sample).decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            continue
        score = _sniff_score(text, kana_counts=enc != "gb18030",
                             hanja_weight=-1.0 if enc == "euc_kr" else 0.2)
        if score > best_score:
            best, best_score = enc, score
    return content.decode(best or "utf-8", errors="replace")
