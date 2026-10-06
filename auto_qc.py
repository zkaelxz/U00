"""
auto_qc.py -- the Auto QC pass: a factual-detail check between each
line's source text and its translation.

Looks for the details a translation most often drops or invents without
anyone noticing -- numbers, dates, times, money amounts, units of
measurement, and the drama's own known names (glossary name terms and
series characters' source-language aliases) -- and flags a line when:

  - one of them is in the source but not, in any recognizable form, in the
    translation ("missing"), or
  - a number is in the translation that isn't in the source at all
    ("extra" -- usually a hallucination, not a legitimate conversion).

Deliberately tolerant, since a flag nobody needed is noise in the review
queue: a number may reappear as digits or English words ("三百" ->
"300"/"three hundred"), a month/weekday by name ("三月" -> "March"), an
ordinal as a word ("第三" -> "third"); a money amount counts as carried over
if the translation has *any* money amount (so "100 yuan" -> "about $14" is
fine), and a measurement if the translation has any measurement (5 公里 ->
"3 miles"). A bare CJK numeral with no counting context is ignored -- 一样,
十分, 千万, 万一 and friends are words, not numbers -- and so is the value 1
(一个 is usually just "a").

Plain regex/parsing, no model call: free, deterministic, and safe to re-run.
Covers Arabic digits in any source language, and Chinese/Japanese kanji
numerals; Korean native (hangul) number words aren't parsed.
"""

import re
import unicodedata

# The flag key this pass sets (see translate_engines.SYSTEM_FLAG_REASONS).
AUTO_QC_FLAG = "factual_detail"

# Glossary categories treated as names that must survive translation.
# Titles, honorifics, techniques etc. are legitimately paraphrased or
# dropped in context, so they aren't checked.
NAME_CATEGORIES = ("person_name", "courtesy_name", "clan_sect", "place")

# ------------------------------------------------------------------ source

_CJK_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
_CJK_BIG = {"万": 10 ** 4, "亿": 10 ** 8}
_CJK_NUM_CHARS = "".join(_CJK_DIGITS) + "".join(_CJK_UNITS) + "".join(_CJK_BIG)

# What may follow a number in the source, longest first within each kind so
# "块钱" wins over "块" and "分钟" is matched (a bare "分" isn't: 十分 means
# "very" far more often than "ten minutes").
_SRC_CURRENCY = ("人民币", "块钱", "美元", "美金", "英镑", "欧元", "日元", "韩元", "港币", "台币",
                 "银子", "文钱", "两银", "金币", "块", "元", "毛", "角", "円", "원")
_SRC_UNITS = ("平方米", "公里", "千米", "公斤", "千克", "厘米", "毫米", "毫升", "英里", "英尺", "公分",
              "平米", "米", "斤", "克", "升", "吨", "度", "里", "寸", "尺", "磅",
              "km", "kg", "cm", "mm", "ml", "m", "g", "l")
_SRC_DATE = ("年", "月", "日", "号")
_SRC_TIME = ("点钟", "小时", "分钟", "秒钟", "点", "时", "秒", "時")
# Counters/measure words -- a numeral before one is a real count. Left out
# on purpose, because they start common idioms more often than counts:
# 周 (四周 "all around"), 成 (八成 "probably"), 折 (打八折 = 20% off), 头
# (三头六臂), 口 (两口子 "a couple"), 世 (三生三世), 番 (三番五次).
_SRC_COUNTERS = ("个", "位", "只", "条", "张", "本", "件", "次", "遍", "天", "岁", "歳", "星期",
                 "倍", "名", "人", "辆", "台", "杯", "瓶", "碗", "层", "楼", "家", "间", "份",
                 "场", "句", "声", "步", "下", "支", "把", "双", "对", "套", "首", "部", "集", "章",
                 "节", "页", "箱", "包", "片", "颗", "粒", "匹", "棵", "座", "所", "架", "艘",
                 "趟", "回", "刀", "拳", "招", "代", "辈", "個", "枚", "階", "%")
_SRC_CURRENCY_PREFIX = ("¥", "$", "€", "£", "₩")
_WEEKDAY_PREFIX = ("星期", "礼拜", "周")

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_ARABIC_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_CJK_RUN_RE = re.compile(f"[{_CJK_NUM_CHARS}]+")
_CJK_CHAR_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def normalize(text: str) -> str:
    # Full-width digits/symbols (０-９, ￥, ％) -> their ASCII forms.
    return unicodedata.normalize("NFKC", text or "")


def parse_cjk_numeral(s: str):
    """一百零五 -> 105, 三万五千 -> 35000, 二〇二四 -> 2024 (a run with no
    十/百/千/万/亿 is read digit by digit, the way years and phone numbers
    are said). None if `s` isn't a numeral run."""
    if not s or any(c not in _CJK_NUM_CHARS for c in s):
        return None
    if len(s) > 1 and not any(c in _CJK_UNITS or c in _CJK_BIG for c in s):
        return int("".join(str(_CJK_DIGITS[c]) for c in s))
    total = section = digit = 0
    for c in s:
        if c in _CJK_DIGITS:
            digit = _CJK_DIGITS[c]
        elif c in _CJK_UNITS:
            section += (digit or 1) * _CJK_UNITS[c]
            digit = 0
        else:
            total += (section + digit) * _CJK_BIG[c]
            section = digit = 0
    return total + section + digit


def _number(text: str) -> float:
    v = float(text.replace(",", ""))
    return int(v) if v.is_integer() else v


def _startswith_any(text: str, words) -> str:
    for w in words:
        if text.startswith(w):
            return w
    return ""


def _endswith_any(text: str, words) -> str:
    for w in words:
        if text.endswith(w):
            return w
    return ""


def _classify(before: str, after: str, value):
    """(kind, alternates) for a number from its surroundings in the source,
    or (None, ()) if nothing marks it as a real quantity. alternates: extra
    English words that also count as carrying it over (a month name, ...)."""
    after = after.lstrip()
    if _endswith_any(before.rstrip(), _SRC_CURRENCY_PREFIX) or _startswith_any(after, _SRC_CURRENCY):
        return "amount", ()
    if _startswith_any(after, _SRC_UNITS):
        return "unit", ()
    if _endswith_any(before, _WEEKDAY_PREFIX) and isinstance(value, int) and 1 <= value <= 6:
        return "date", (_WEEKDAYS[value - 1],)
    if after.startswith("月") and isinstance(value, int) and 1 <= value <= 12:
        return "date", (_MONTHS[value - 1],)
    if _startswith_any(after, _SRC_DATE):
        return "date", ()
    if _startswith_any(after, _SRC_TIME):
        return "time", ()
    if before.endswith("第"):
        return "number", ()
    if before.endswith("百分之") or _startswith_any(after, _SRC_COUNTERS):
        return "number", ()
    return None, ()


def source_numbers(src: str):
    """Every number the source states. Returns (checked, pool):
    checked -- [(text, value, kind, alternates)] for numbers the translation
               is expected to carry over;
    pool    -- every numeric value mentioned in any form (including bare
               CJK numerals that aren't checked), for the reverse check."""
    src = normalize(src)
    checked, pool = [], set()
    for m in _ARABIC_RE.finditer(src):
        value = _number(m.group())
        after = src[m.end():]
        # 3万 / 1.5亿 / 5千 (but not 5千米 = 5 km, or 5千克 = 5 kg)
        big = _startswith_any(after, ("万", "亿", "千"))
        if big and not (big == "千" and after[1:2] in ("米", "克", "瓦")):
            value = _number(str(value * {"万": 10 ** 4, "亿": 10 ** 8, "千": 1000}[big]))
            after = after[1:]
        pool.add(value)
        kind, alts = _classify(src[:m.start()], after, value)
        checked.append((m.group() + (big or ""), value, kind or "number", alts))

    consumed = 0  # end of the last 三点五-style fraction, already part of a decimal
    for m in _CJK_RUN_RE.finditer(src):
        if m.start() < consumed:
            continue
        run, end = m.group(), m.end()
        value = parse_cjk_numeral(run)
        after = src[end:]
        extra = []
        # 三点五 = 3.5, but 三点二十(分) / 三点半 / 三点一刻 are clock times.
        if after.startswith("点"):
            frac = _CJK_RUN_RE.match(src, end + 1)
            if after[1:2] == "半":
                extra.append(30)
            elif after[1:3] == "一刻":
                extra.append(15)
            elif after[1:3] == "三刻":
                extra.append(45)
            elif frac and not any(c in _CJK_UNITS for c in frac.group()) \
                    and not src[frac.end():].startswith("分"):
                value = float(f"{value}." + "".join(str(_CJK_DIGITS.get(c, 0))
                                                     for c in frac.group()))
                after = src[frac.end():]
                consumed = frac.end()
        pool.add(value)
        pool.update(extra)
        kind, alts = _classify(src[:m.start()], after, value)
        # "Can't be a real quantity" beats "might be": 一 (usually just "a"),
        # and a bare run with nothing counting it -- 一样/十分/千万/万一.
        if kind is None or value in (0, 1):
            continue
        checked.append((run, value, kind, alts))
    # 俩/双/对 are how "two"/"both"/"a pair" are often said.
    if any(c in src for c in "俩双对"):
        pool.add(2)
    return checked, pool


# ----------------------------------------------------------------- target

_EN_SMALL = {w: i for i, w in enumerate(
    ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
     "fourteen fifteen sixteen seventeen eighteen nineteen").split())}
_EN_TENS = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_EN_SCALES = {"hundred": 100, "thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9,
              "grand": 1000, "k": 1000}
_EN_ORDINALS = {w: i + 1 for i, w in enumerate(
    ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
     "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth "
     "twentieth").split())}
_EN_ORDINALS.update({"thirtieth": 30, "hundredth": 100, "thousandth": 1000})

# Words that carry a small number without saying it ("两次" -> "twice").
_VALUE_WORDS = {2: ("both", "couple", "pair", "twice", "double", "twin", "twins"),
                3: ("thrice", "triple"), 12: ("dozen",)}

_TGT_CURRENCY = {"dollar", "dollars", "buck", "bucks", "yuan", "rmb", "cny", "usd", "yen", "won",
                 "euro", "euros", "pound", "pounds", "cent", "cents", "grand", "coin", "coins",
                 "tael", "taels", "silver", "gold", "quid", "hkd", "ntd"}
_TGT_CURRENCY_PREFIX = {"$", "€", "£", "¥", "₩"}
_TGT_UNITS = {"km", "kms", "kilometer", "kilometers", "kilometre", "kilometres", "mile", "miles",
              "m", "meter", "meters", "metre", "metres", "cm", "centimeter", "centimeters",
              "centimetre", "centimetres", "mm", "inch", "inches", "foot", "feet", "ft", "yard",
              "yards", "kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "g", "gram", "grams",
              "lb", "lbs", "pound", "pounds", "ounce", "ounces", "oz", "ton", "tons", "tonne",
              "tonnes", "l", "liter", "liters", "litre", "litres", "ml", "gallon", "gallons",
              "degree", "degrees", "°", "li", "jin", "catty", "catties", "sq", "square"}

_TGT_TOKEN_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|[A-Za-z]+|[$€£¥₩%°]")


def target_numbers(tgt: str):
    """Every number in the translation: [(text, value, is_word, context)],
    context being "amount" (a $ before or "dollars"/"yuan"/... after),
    "unit" (a measurement word after) or None. Digits and number words
    ("three hundred and five", "30 thousand", "a hundred") both count."""
    tokens = [(m.group(), m.group().lower()) for m in _TGT_TOKEN_RE.finditer(normalize(tgt))]
    out = []
    i = 0
    while i < len(tokens):
        raw, tok = tokens[i]
        start = i
        value, is_word = None, False
        if tok[0].isdigit():
            value = _number(tok)
            i += 1
        elif tok in _EN_SMALL or tok in _EN_TENS or (
                tok == "a" and i + 1 < len(tokens) and tokens[i + 1][1] in ("hundred", "thousand",
                                                                              "million", "billion")):
            is_word, value, current = True, 0, 0
            last = None  # "small" / "tens" / "scale", to split "one two" into two numbers
            while i < len(tokens):
                w = tokens[i][1]
                if w == "a" and last is None:
                    current = 1
                elif w in _EN_TENS and last in (None, "scale", "hundred"):
                    current += _EN_TENS[w]
                elif w in _EN_SMALL and (last in (None, "scale", "hundred") or
                                         (last == "tens" and _EN_SMALL[w] < 10)):
                    current += _EN_SMALL[w]
                elif w == "hundred" and last in ("small", "tens", "a"):
                    current *= 100
                elif w in ("thousand", "million", "billion") and last in ("small", "tens", "a",
                                                                          "hundred"):
                    value += current * _EN_SCALES[w]
                    current = 0
                elif w == "and" and last in ("hundred", "scale") and i + 1 < len(tokens) \
                        and (tokens[i + 1][1] in _EN_SMALL or tokens[i + 1][1] in _EN_TENS):
                    i += 1
                    continue
                else:
                    break
                last = ("a" if w == "a" else "tens" if w in _EN_TENS else "small" if w in _EN_SMALL
                        else "hundred" if w == "hundred" else "scale")
                i += 1
            value += current
        else:
            i += 1
            continue
        # A number followed by a scale: "30 thousand", "5k", "2.5 million", "five grand".
        if i < len(tokens) and tokens[i][1] in _EN_SCALES:
            value = _number(str(value * _EN_SCALES[tokens[i][1]]))
            i += 1
        text = " ".join(t[0] for t in tokens[start:i])
        before = tokens[start - 1][1] if start else ""
        # One word may sit in between: "three more miles", "300 more yuan".
        after = {t[1] for t in tokens[i:i + 2]}
        if before in _TGT_CURRENCY_PREFIX or after & _TGT_CURRENCY or tokens[i - 1][1] == "grand":
            context = "amount"
        elif after & _TGT_UNITS:
            context = "unit"
        else:
            context = None
        out.append((text, value, is_word, context))
    return out


def _target_words(tgt: str) -> set:
    return set(re.findall(r"[a-z]+", normalize(tgt).lower()))


# ------------------------------------------------------------------ names

def build_name_list(glossary_terms=None, series_characters=None) -> list:
    """[(source_forms, target_forms)] of the names Auto QC checks: glossary
    terms in a name category, and series characters that have a
    source-language (CJK) alias recorded. Single-character source forms are
    left out -- one character matches inside too many unrelated words.

    A glossary term's recorded aliases (alt spellings/
    transliterations of term_original) are folded in the same way
    series_characters.aliases already is below -- classified by whether
    each alias contains a CJK character: a CJK alias is another source
    form (e.g. an alternate way the same name is written in the original),
    a non-CJK one is another target form (e.g. an alternate romanization
    that might show up in the translation instead of the canonical
    term_translation)."""
    names = []
    for t in glossary_terms or []:
        orig, trans = (t.get("term_original") or "").strip(), (t.get("term_translation") or "").strip()
        if t.get("category") in NAME_CATEGORIES and len(orig) > 1 and trans:
            aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
            src_forms = tuple([orig] + [a for a in aliases if _CJK_CHAR_RE.search(a) and len(a) > 1])
            tgt_forms = tuple([trans] + [a for a in aliases if not _CJK_CHAR_RE.search(a)])
            names.append((src_forms, tgt_forms))
    for c in series_characters or []:
        aliases = [a.strip() for a in re.split(r"[|,，、]", c.get("aliases") or "") if a.strip()]
        src_forms = tuple(a for a in aliases if _CJK_CHAR_RE.search(a) and len(a) > 1)
        tgt_forms = tuple(a for a in [c.get("character_name") or ""] + aliases
                          if a and not _CJK_CHAR_RE.search(a))
        if src_forms and tgt_forms:
            names.append((src_forms, tgt_forms))
    return names


def build_banned_terms(glossary_terms=None) -> list:
    """[(source_forms, banned_forms)] for glossary terms with a recorded
    banned_translations list -- checked across every category,
    not just NAME_CATEGORIES, since a prohibited rendering isn't limited to
    names the way the "missing" name check is. source_forms includes both
    term_original and any recorded aliases, so a line using an alt spelling
    of the term still gets its banned-translation list checked.

    This is a new, independent, flag-only mechanism: Auto QC uses it to
    flag a line for review, never to rewrite it. It does not read
    `notes` and has nothing to do with the older enforce_exact hard
    find-replace in translation_guide.apply_hard_term_substitutions,
    which is untouched by this and stays enforce_exact-only."""
    out = []
    for t in glossary_terms or []:
        banned = [b.strip() for b in re.split(r"[|,，、]", t.get("banned_translations") or "")
                  if b.strip()]
        if not banned:
            continue
        orig = (t.get("term_original") or "").strip()
        aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
        src_forms = tuple(f for f in [orig] + aliases if f)
        if src_forms:
            out.append((src_forms, tuple(banned)))
    return out


def banned_hit(banned_forms, tgt_lower: str):
    """The first banned variant found in the (already-lowercased) target
    text, or None."""
    for form in banned_forms:
        f = form.lower().strip()
        if f and re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", tgt_lower):
            return form
    return None


def _name_carried(target_forms, tgt_lower: str, tgt_words: set) -> bool:
    for form in target_forms:
        f = form.lower()
        if re.search(r"(?<![a-z])" + re.escape(f) + r"(?![a-z])", tgt_lower):
            return True
        # Part of a full name is enough -- "Lin Wanwan" is often just "Wanwan".
        if any(len(part) > 2 and part in tgt_words for part in re.findall(r"[a-z]+", f)):
            return True
    return False


# ------------------------------------------------------------------ check

def check_line(src: str, tgt: str, names=(), banned_terms=()) -> list:
    """Every factual-detail mismatch between one source line and its
    translation: [{"direction": "missing"|"extra"|"banned", "kind": ...,
    "text": ...}]. Empty when there's nothing to compare (either side
    blank) or when everything checks out.

    `banned_terms` (from build_banned_terms()) is checked
    separately from `names`: a term whose source form appears in `src` but
    whose translation in `tgt` matches one of its recorded
    banned_translations is flagged with direction "banned" -- never
    rewritten, only reported, same as every other Auto QC issue."""
    if not (src or "").strip() or not (tgt or "").strip():
        return []
    issues = []
    checked, pool = source_numbers(src)
    tnums = target_numbers(tgt)
    tvalues = {v for _, v, _, _ in tnums}
    twords = _target_words(tgt)
    tvalues |= {v for w, v in _EN_ORDINALS.items() if w in twords}
    has_amount = any(ctx == "amount" for *_, ctx in tnums)
    has_unit = any(ctx == "unit" for *_, ctx in tnums)

    seen = set()
    for text, value, kind, alts in checked:
        if value in (0, 1) or (text, value) in seen:
            continue
        seen.add((text, value))
        if value in tvalues or any(a in twords for a in alts + _VALUE_WORDS.get(value, ())):
            continue
        if kind == "amount" and has_amount or kind == "unit" and has_unit:
            continue  # converted (100 yuan -> about $14), not dropped
        issues.append({"direction": "missing", "kind": kind, "text": text})

    src_kinds = {kind for *_, kind, _ in checked}
    cjk_source = bool(_CJK_CHAR_RE.search(src))
    seen_extra = set()
    for text, value, is_word, ctx in tnums:
        # "one" is a pronoun far more often than a number, and a word number
        # can only be matched against a source whose numerals we can read.
        if (is_word and (text.lower() in ("one", "zero") or not cjk_source)) or value == 0:
            continue
        if value in pool or value in seen_extra:
            continue
        if ctx == "amount" and "amount" in src_kinds or ctx == "unit" and "unit" in src_kinds:
            continue
        seen_extra.add(value)
        issues.append({"direction": "extra", "kind": "number", "text": text})

    if names:
        src_norm, tgt_lower = normalize(src), normalize(tgt).lower()
        masked = src_norm
        # Longest forms first, masking each hit, so a full name isn't also
        # re-checked as the shorter name inside it.
        for src_forms, tgt_forms in sorted(names, key=lambda n: -max(len(f) for f in n[0])):
            hit = next((f for f in sorted(src_forms, key=len, reverse=True) if f in masked), None)
            if hit is None:
                continue
            masked = masked.replace(hit, "\0" * len(hit))
            if not _name_carried(tgt_forms, tgt_lower, twords):
                issues.append({"direction": "missing", "kind": "name", "text": hit})

    if banned_terms:
        src_norm2 = normalize(src).lower()
        tgt_lower2 = normalize(tgt).lower()
        for src_forms, banned_forms in banned_terms:
            if any(f.lower() in src_norm2 for f in src_forms):
                hit = banned_hit(banned_forms, tgt_lower2)
                if hit:
                    issues.append({"direction": "banned", "kind": "banned_translation", "text": hit})
    return issues


_KIND_LABELS = {"number": "number", "date": "date", "time": "time", "amount": "amount",
                "unit": "measurement", "name": "name"}


def issue_note(issues) -> str:
    """The flag_note for a flagged line -- one plain sentence per direction,
    naming exactly what's missing/extra, in the same explain-then-advise
    tone as the app's other system flags."""
    parts = []
    missing = [f"{i['text']} ({_KIND_LABELS.get(i['kind'], i['kind'])})"
               for i in issues if i["direction"] == "missing"]
    extra = [i["text"] for i in issues if i["direction"] == "extra"]
    banned = [i["text"] for i in issues if i["direction"] == "banned"]
    if missing:
        parts.append("In the source but not the translation: " + ", ".join(missing) + ".")
    if extra:
        parts.append("In the translation but not the source: " + ", ".join(extra) + ".")
    if banned:
        parts.append("Uses a translation flagged as prohibited: " + ", ".join(banned) + ".")
    parts.append("Check the translation says the same thing.")
    return " ".join(parts)


def find_issues(lines, names=(), banned_terms=()) -> list:
    """[(line, issues)] for every line with a mismatch -- read-only, for
    showing what Auto QC would flag without writing anything."""
    out = []
    for ln in lines:
        issues = check_line(ln.zh, ln.en, names, banned_terms)
        if issues:
            out.append((ln, issues))
    return out


def run_auto_qc(lines, names=(), banned_terms=()) -> dict:
    """Checks every line in place and updates its flag:
      - a line with a mismatch and no flag gets AUTO_QC_FLAG + a note;
      - a line already flagged by Auto QC gets its note refreshed, or the
        flag cleared if it no longer has a mismatch (it was fixed);
      - a line flagged for some other reason is left alone -- Auto QC never
        replaces another check's flag -- and counted in already_flagged.
    Returns {"flagged", "cleared", "already_flagged", "checked"}."""
    flagged = cleared = already = checked = 0
    for ln in lines:
        if not (ln.zh or "").strip() or not (ln.en or "").strip():
            if ln.flag == AUTO_QC_FLAG:
                ln.flag, ln.flag_note = None, ""
                cleared += 1
            continue
        checked += 1
        issues = check_line(ln.zh, ln.en, names, banned_terms)
        if issues:
            if ln.flag in (None, "", AUTO_QC_FLAG):
                ln.flag, ln.flag_note = AUTO_QC_FLAG, issue_note(issues)
                flagged += 1
            else:
                already += 1
        elif ln.flag == AUTO_QC_FLAG:
            ln.flag, ln.flag_note = None, ""
            cleared += 1
    return {"flagged": flagged, "cleared": cleared, "already_flagged": already, "checked": checked}
