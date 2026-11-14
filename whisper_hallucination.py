"""
whisper_hallucination.py -- drops the segments Whisper invents on silence or
background noise: stock credit/outro phrases and repeated-phrase loops.
"""

import re


# Credit/outro lines Whisper emits on silence because its training subtitles
# ended with them. Matched against the whole segment text only, after
# _stock_key() strips case, spaces and punctuation.
_STOCK_PHRASE_RES = [re.compile(p) for p in (
    r"subtitlesbytheamaraorgcommunity",
    r"amaraorg.{0,8}",
    r"thanks?youforwatching",
    r"thanksforwatching",
    r"(?:感谢|感謝|谢谢|謝謝)(?:您的?)?(?:观看|觀看)",
    r"字幕由.{0,30}提供",
    r"由amaraorg社[群區区]提供的?字幕",
    r"[请請]不吝[点點][赞贊讚].*",
    r"ご視聴ありがとうございま(?:した|す)",
    r"ご清聴ありがとうございま(?:した|す)",
    r"mbc뉴스.{0,12}입니다",
    r"시청해주셔서감사합니다",
    r"(?:中文)?字幕志愿者.{0,20}",
    r".{0,20}明[镜鏡][与與][点點][点點][栏欄]目",
    r"字幕by.{0,20}",
    r"afewmomentslater",
    r"(?:优优独播剧场|中文字幕)?yo(?:yo)?televisionseriesexclusive",
    r"(?:ぜひ)?チャンネル登録.{0,20}",
    r"구독과?좋아요.{0,20}",
    r"(?:mingpao(?:canada|toronto|news)?)+",
    r"(?:please|dontforgetto)?(?:likeand)?subscribe.{0,30}",
    r"(?:please)?(?:likeand)?subscribetomychannel(?:and(?:hit|click|press)thebell(?:icon)?(?:tobenotified)?)?",
)]
# Credit-like lines that are not a whole stock phrase ("本期影片就到這裏,謝謝觀看,記得訂閱、按讚、分享唷"
# or "please subscribe, press the like button"; the English form must open like an appeal, so that
# "I told him to subscribe to my channel" stays unflagged). A streamer can really say these, so they are only
# flagged, never dropped; the length cap keeps a long real sentence that mentions them out.
_CREDIT_LIKE_RES = [re.compile(p) for p in (
    r".*(?:訂閱|订阅).*(?:按讚|按赞|點贊|点赞|點讚|分享|轉發|转发).*",
    r".*(?:按讚|按赞|點贊|点赞|點讚).*(?:訂閱|订阅).*",
    r"(?:please|dontforget|remember|like|subscribe|soattheend|ifyou).*subscribe.*(?:like|share|channel|bell).*",
    r".*amaraorg.*",
)]
CREDIT_LIKE_MAX_LETTERS = 100
# No title this app transcribes is Russian or Greek; a line in those scripts is Whisper writing
# invented text over unclear audio.
_FOREIGN_SCRIPT_RE = re.compile(r"[Ͱ-ϿЀ-ӿ]")
STOCK_LINE_FLAG_NOTE = "Looks like a credit line Whisper writes on silence; check against the audio"
FOREIGN_SCRIPT_FLAG_NOTE = "Text in a script this audio is unlikely to hold; check against the audio"
# A stock phrase is only dropped with at least this much silence on each side
# (a missing neighbour counts as silence), so dialogue that happens to say
# "thanks for watching" between other lines survives.
STOCK_PHRASE_ISOLATION_SEC = 5.0
# The last line of a file has nothing after it to confirm the silence, and an outro
# credit sits right after the last real line, so it needs less silence before it.
FINAL_STOCK_PHRASE_ISOLATION_SEC = 3.0
# Whisper loops inside one segment too ("谢谢谢谢…"). A run of one character is
# only a loop at a higher count than a longer phrase: "哈哈哈哈" and "..." are
# real speech, "好的好的好的好的" almost never is.
LOOP_MIN_REPEATS_SINGLE_CHAR = 10
LOOP_MIN_REPEATS_PHRASE = 4
# Longest phrase treated as the unit of a loop; anything longer repeated that
# often is a quoted passage, not a decoder stuck on one phrase.
LOOP_MAX_PHRASE_CHARS = 12
# A short chant ("no no no no", "Go! Go! Go! Go!", "はい。はい。はい。はい。") is
# four copies of a 1-3 letter word: real speech. A stuck decoder runs on.
LOOP_MIN_PHRASE_RUN_CHARS = 12
# The same bar for separate segments: a one-character answer ("有", "没有") said back to
# back is ordinary dialogue and Whisper tiles it as one segment per copy, so only a longer
# run is a stuck decoder. Shorter runs stay and the chatter screen flags them for review.
# A limit lowered below the default is the user asking for shorter runs to collapse, so it
# applies to single characters too.
DEFAULT_REPEAT_COUNT = 4
# A loop on silence or music comes out as copies right after one another; the same
# short reply heard minutes apart ("はい" four times in a scene) is real speech, so a
# gap longer than this between two copies ends the run.
REPEAT_MAX_GAP_SEC = 10.0
# Copies kept only because long gaps split them up: real replies, or a loop over music
# where each burst came out alone, so they go to review rather than being dropped.
REPEAT_GAP_NOTE = "check: the same line came back several times with long gaps; check it is real speech"
REPEAT_MIN_RUN_SINGLE_CHAR = LOOP_MIN_REPEATS_SINGLE_CHAR
_LOOP_RES = (
    re.compile(r"(.)\1{%d,}" % (LOOP_MIN_REPEATS_SINGLE_CHAR - 1), re.S),
    re.compile(r"(.{2,%d}?)\1{%d,}" % (LOOP_MAX_PHRASE_CHARS, LOOP_MIN_REPEATS_PHRASE - 1), re.S),
)


# On hard audio (music, noise, overlapping voices) with the skip rule relaxed, Whisper
# can write a run of short invented items ("凤凰、約翰、酷光、…") that scores confident
# on every segment, so neither its own thresholds nor the loop checks above see it.
LIST_MIN_ITEMS = 6
LIST_MAX_ITEM_LETTERS = 6
# A list this dense in a short time is not something a person said: real speech tops out
# around 8-10 letters a second, and a list of names read that fast is invented text.
LIST_DROP_MIN_CHARS_PER_SEC = 8.0
# Alone, a rate above this is flagged for review, not dropped: fast real speech and
# a wrong time range both reach it.
IMPLAUSIBLE_CHARS_PER_SEC = 15.0
IMPLAUSIBLE_FLAG = "text_uncertain"
IMPLAUSIBLE_LIST_NOTE = "Looks like a list of invented words; check against the audio"
IMPLAUSIBLE_RATE_NOTE = "More text than this stretch of audio can hold; check against the audio"
# Whisper on noise can also chatter single characters ("無", "有", "無", …) that no
# repeat check catches because the text alternates. One such line is real speech ("有");
# a run of them is not.
CHATTER_MIN_RUN = 3
CHATTER_MAX_LETTERS = 1
IMPLAUSIBLE_CHATTER_NOTE = "One-character lines in a row, often noise; check against the audio"
# Fewer seconds than this cannot hold a word, so several letters in it are a guess.
MIN_WORD_SEC = 0.5
# Whisper on a voiced but unintelligible stretch can also cut it into slivers
# ("ota", "request", "罪" in 0.1 s each), too short to hold a word.
SLIVER_SEC = 0.3
IMPLAUSIBLE_SLIVER_NOTE = "Cut into slivers of audio, often unclear speech; check against the audio"
IMPLAUSIBLE_BLINK_NOTE = "Text in a split second of audio; check against the audio"
_LIST_SPLIT_RE = re.compile(r"[、,，;；]")
# Letters a second is only meaningful for scripts that write one character per syllable;
# a Latin line of the same duration holds several times as many letters.
_CJK_CHAR_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
# Shorter than this the rate is dominated by the timing's rounding.
_MIN_RATE_SEC = 0.5


def _is_item_list(text):
    items = [i for i in (_stock_key(p) for p in _LIST_SPLIT_RE.split(text or "")) if i]
    return (len(items) >= LIST_MIN_ITEMS
            and all(len(i) <= LIST_MAX_ITEM_LETTERS for i in items))


def _chars_per_sec(seg):
    try:
        length = seg["end"] - seg["start"]
    except KeyError:
        return 0.0
    if length < _MIN_RATE_SEC:
        return 0.0
    return len(_CJK_CHAR_RE.findall(seg["text"])) / length


def _flag(seg, note):
    return seg if seg.get("flag") else {**seg, "flag": IMPLAUSIBLE_FLAG, "flag_note": note}


def _flag_chatter_and_blinks(segments):
    """`segments` with each run of CHATTER_MIN_RUN or more one-character segments, and each
    sub-MIN_WORD_SEC segment holding 3 or more letters, flagged IMPLAUSIBLE_FLAG."""
    # Only CJK characters: a Latin one-letter line ("I", "x") is an ordinary word or a test stub.
    tiny = [len(_CJK_CHAR_RE.findall(s["text"])) == CHATTER_MAX_LETTERS == _letters_in(s)
            for s in segments]
    out, i = list(segments), 0
    while i < len(out):
        j = i
        while j < len(out) and tiny[j]:
            j += 1
        if j - i >= CHATTER_MIN_RUN:
            out[i:j] = [_flag(s, IMPLAUSIBLE_CHATTER_NOTE) for s in out[i:j]]
        i = max(j, i + 1)
    out = _flag_slivers(out)
    for k, seg in enumerate(out):
        try:
            blink = seg["end"] - seg["start"] < MIN_WORD_SEC and _letters_in(seg) >= 3
        except KeyError:
            blink = False
        if blink:
            out[k] = _flag(seg, IMPLAUSIBLE_BLINK_NOTE)
    return out


def _flag_slivers(segments):
    """`segments` with each one under SLIVER_SEC long flagged: too short to hold a word."""
    return [_flag(s, IMPLAUSIBLE_SLIVER_NOTE)
            if "start" in s and "end" in s and 0 < s["end"] - s["start"] < SLIVER_SEC else s
            for s in segments]


def _letters_in(seg):
    return len(_stock_key(seg["text"]))


def screen_implausible_text(segments, drop_lists: bool = True):
    """`segments` without the dense item lists no one could have said (a list of short
    items at LIST_DROP_MIN_CHARS_PER_SEC or more), and the other lists and over-fast
    segments flagged IMPLAUSIBLE_FLAG, unless they carry a flag already. With
    `drop_lists` off the dense lists are flagged too."""
    out = []
    for seg in segments:
        rate = _chars_per_sec(seg)
        is_list = _is_item_list(seg["text"])
        if drop_lists and is_list and rate >= LIST_DROP_MIN_CHARS_PER_SEC:
            import applog
            applog.get_logger().info(
                f"invented list dropped: segment {seg['start']:.1f}-{seg['end']:.1f}s "
                f"({rate:.1f} characters/s)")
            continue
        if (is_list or rate >= IMPLAUSIBLE_CHARS_PER_SEC) and not seg.get("flag"):
            seg = {**seg, "flag": IMPLAUSIBLE_FLAG,
                   "flag_note": IMPLAUSIBLE_LIST_NOTE if is_list else IMPLAUSIBLE_RATE_NOTE}
        out.append(seg)
    return _flag_chatter_and_blinks(out)


def _stock_key(text):
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _first_loop(key):
    """The earliest loop in `key` (letters only) as a match, or None."""
    best = None
    for rx in _LOOP_RES:
        pos = 0
        while (m := rx.search(key, pos)):
            single = rx is _LOOP_RES[0]
            # A run of one letter is the single-character rule's to judge, not
            # a two-letter "phrase" that happens to fit it.
            if single or (len(set(m.group(1))) > 1 and m.end() - m.start() > LOOP_MIN_PHRASE_RUN_CHARS):
                if best is None or m.start() < best.start():
                    best = m
                break
            pos = m.start() + 1
    return best


def collapse_phrase_loop(text):
    """`text` with each run of a phrase repeated past the LOOP_* counts cut
    down to its first copy; punctuation between copies is ignored when
    matching and dropped with the run. Only letters count, so a number such as
    100,000,000 is never a loop. Other text is untouched."""
    while True:
        # Case-folded per character so the key keeps one entry per kept character.
        kept = [(i, ch.lower() if len(ch.lower()) == 1 else ch)
                for i, ch in enumerate(text) if ch.isalpha()]
        m = _first_loop("".join(ch for _, ch in kept))
        if m is None:
            return text
        unit_end = kept[m.start() + len(m.group(1)) - 1][0] + 1
        run_end = kept[m.end() - 1][0] + 1
        text = text[:unit_end] + text[run_end:]


def is_credit_like(text):
    """Whether `text` reads like a credit/outro line Whisper invents, without being a whole
    stock phrase: subscribe/like/share wording, or an Amara.org mention."""
    key = _stock_key(text)
    return len(key) <= CREDIT_LIKE_MAX_LETTERS and any(rx.fullmatch(key) for rx in _CREDIT_LIKE_RES)


def _flag_invented_text(segments):
    """`segments` with each kept stock phrase, credit-like line or line in a foreign script
    flagged IMPLAUSIBLE_FLAG (unless it carries a flag already). Nothing is dropped: the stock
    phrase reaches here only because it sits next to speech, where it may be real."""
    out = []
    for seg in segments:
        text = seg["text"]
        if is_stock_phrase(text) or is_credit_like(text):
            seg = _flag(seg, STOCK_LINE_FLAG_NOTE)
        elif _FOREIGN_SCRIPT_RE.search(text):
            seg = _flag(seg, FOREIGN_SCRIPT_FLAG_NOTE)
        out.append(seg)
    return out


def is_stock_phrase(text):
    """Whether the whole of `text` is one of the credit/outro phrases Whisper invents."""
    key = _stock_key(text)
    return any(rx.fullmatch(key) for rx in _STOCK_PHRASE_RES)


def _is_isolated_stock_phrase(segments, i, isolation_sec=STOCK_PHRASE_ISOLATION_SEC):
    if not is_stock_phrase(segments[i]["text"]):
        return False
    seg = segments[i]
    try:
        before = isolation_sec if i + 1 < len(segments) else min(isolation_sec, FINAL_STOCK_PHRASE_ISOLATION_SEC)
        if i > 0 and seg["start"] - segments[i - 1]["end"] < before:
            return False
        if i + 1 < len(segments) and segments[i + 1]["start"] - seg["end"] < isolation_sec:
            return False
    except KeyError:
        return False
    return True


def _without_phrase_loop(seg):
    text = collapse_phrase_loop(seg["text"])
    if text == seg["text"]:
        return seg
    # Nothing else says text was removed, so leave a trace (never the text itself).
    import applog
    applog.get_logger().info(
        f"loop collapse: segment {seg.get('start', 0):.1f}-{seg.get('end', 0):.1f}s lost "
        f"{len(seg['text']) - len(text)} characters")
    # The word timings spelled the longer text, so they no longer fit.
    return {k: v for k, v in seg.items() if k != "words"} | {"text": text}


def _close_in_time(prev, seg) -> bool:
    """Whether `seg` follows `prev` within REPEAT_MAX_GAP_SEC; True when either has no
    times (a caller passing bare text pieces keeps the old back-to-back rule)."""
    try:
        return seg["start"] - prev["end"] <= REPEAT_MAX_GAP_SEC
    except (KeyError, TypeError):
        return True


def filter_hallucinated_segments(segments, min_repeat_count: int = DEFAULT_REPEAT_COUNT,
                                 stock_isolation_sec: float = STOCK_PHRASE_ISOLATION_SEC,
                                 drop_lists: bool = True):
    """
    Whisper is well known to loop a short phrase across MANY separate
    segments when fed silence or background music -- often a training-
    data artifact like a stock "please subscribe" phrase, or just a
    single word repeated -- and this is a DIFFERENT failure mode from
    what faster-whisper's own decoding thresholds (compression_ratio,
    log_prob, no_speech -- already applied internally before this ever
    sees the output) catch: each individual repeated segment can score
    perfectly confident on its own, since the model isn't uncertain about
    a phrase it's hallucinating with full conviction. It's the SEQUENCE
    of many identical segments in a row that's the actual tell, not any
    one segment's own score -- which is why this needs its own pass
    working on the already-decoded segment list, not another decoding
    parameter.

    Collapses a run of `min_repeat_count` or more CONSECUTIVE segments
    with identical (whitespace-normalized) text, each within
    REPEAT_MAX_GAP_SEC of the one before, down to just the first
    occurrence (and logs how many copies went), on the assumption real speech repeated verbatim that many
    times back-to-back AS SEPARATE SEGMENTS (not within one segment's own
    text -- quick real repetition like "no no no no" almost always comes
    back as one segment) is far less likely than a hallucination loop.

    Trade-off, stated plainly: the rare genuine case of someone actually
    repeating one short line several times, each landing as its own
    segment, gets collapsed to a single line here. min_repeat_count
    defaults conservatively high (4) to keep that risk low -- lower it if
    loops are still getting through on your content, raise it if you've
    actually hit the false-positive case.

    Inside one segment, a phrase repeated past the LOOP_* counts is cut to
    its first copy (collapse_phrase_loop).

    Also drops a segment whose WHOLE text is a stock credit/outro phrase
    ("Thanks for watching", "字幕由…提供", "ご視聴ありがとうございました") when it
    has at least `stock_isolation_sec` (STOCK_PHRASE_ISOLATION_SEC) of silence on each side.
    """
    if not segments:
        return segments

    # Punctuation-insensitive: Whisper writes the same looped phrase with and
    # without its full stop.
    _norm = _stock_key

    segments = [_without_phrase_loop(seg) for seg in segments]
    out = []
    i = 0
    n = len(segments)
    while i < n:
        norm = _norm(segments[i]["text"])
        # The whole run of the same text, gaps or not; the gaps then split it.
        end = i + 1
        while end < n and norm != "" and _norm(segments[end]["text"]) == norm:
            end += 1
        # A count under 2 would collapse every ordinary line; it means "off".
        needed = min_repeat_count
        if needed >= DEFAULT_REPEAT_COUNT and len(_CJK_CHAR_RE.findall(segments[i]["text"])) == len(norm) == 1:
            needed = max(needed, REPEAT_MIN_RUN_SINGLE_CHAR)
        loops = min_repeat_count >= 2
        run_length = end - i
        kept = []
        while i < end:
            j = i + 1
            while j < end and _close_in_time(segments[j - 1], segments[j]):
                j += 1
            if loops and (j - i) >= needed:
                kept.append(segments[i])
                # Nothing else says speech was removed, so leave a trace (never the text).
                import applog
                applog.get_logger().info(
                    f"repeat filter: {j - i - 1} copies of one line removed after "
                    f"{segments[i].get('start', 0):.1f}s (to {segments[j - 1].get('end', 0):.1f}s)")
            else:
                kept.extend(segments[i:j])
            i = j
        if loops and run_length >= needed and len(kept) > 1:
            kept = kept[:1] + [seg if seg.get("flag") else {**seg, "flag": IMPLAUSIBLE_FLAG, "flag_note": REPEAT_GAP_NOTE}
                               for seg in kept[1:]]
        out.extend(kept)
    kept = [seg for k, seg in enumerate(out) if not _is_isolated_stock_phrase(out, k, stock_isolation_sec)]
    return screen_implausible_text(_flag_invented_text(kept), drop_lists)
