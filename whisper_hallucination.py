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
    r"字幕由.{0,30}提供",
    r"请不吝点赞.*",
    r"ご視聴ありがとうございま(?:した|す)",
    r"ご清聴ありがとうございま(?:した|す)",
    r"mbc뉴스.{0,12}입니다",
    r"시청해주셔서감사합니다",
    r"(?:中文)?字幕志愿者.{0,20}",
    r".{0,20}明镜与点点栏目",
    r"(?:优优独播剧场|中文字幕)?yo(?:yo)?televisionseriesexclusive",
    r"(?:ぜひ)?チャンネル登録.{0,20}",
    r"구독과?좋아요.{0,20}",
    r"(?:please|dontforgetto)?(?:likeand)?subscribe.{0,30}",
)]
# A stock phrase is only dropped with at least this much silence on each side
# (a missing neighbour counts as silence), so dialogue that happens to say
# "thanks for watching" between other lines survives.
STOCK_PHRASE_ISOLATION_SEC = 5.0
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
_LOOP_RES = (
    re.compile(r"(.)\1{%d,}" % (LOOP_MIN_REPEATS_SINGLE_CHAR - 1), re.S),
    re.compile(r"(.{2,%d}?)\1{%d,}" % (LOOP_MAX_PHRASE_CHARS, LOOP_MIN_REPEATS_PHRASE - 1), re.S),
)


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
            if single or (len(set(m.group(1))) > 1 and m.end() - m.start() >= LOOP_MIN_PHRASE_RUN_CHARS):
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


def _is_isolated_stock_phrase(segments, i, isolation_sec=STOCK_PHRASE_ISOLATION_SEC):
    key = _stock_key(segments[i]["text"])
    if not any(rx.fullmatch(key) for rx in _STOCK_PHRASE_RES):
        return False
    seg = segments[i]
    try:
        if i > 0 and seg["start"] - segments[i - 1]["end"] < isolation_sec:
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


def filter_hallucinated_segments(segments, min_repeat_count: int = 4,
                                 stock_isolation_sec: float = STOCK_PHRASE_ISOLATION_SEC):
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
    with identical (whitespace-normalized) text down to just the first
    occurrence, on the assumption real speech repeated verbatim that many
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
        j = i + 1
        while j < n and norm != "" and _norm(segments[j]["text"]) == norm:
            j += 1
        # A count under 2 would collapse every ordinary line; it means "off".
        if min_repeat_count >= 2 and (j - i) >= min_repeat_count:
            out.append(segments[i])
        else:
            out.extend(segments[i:j])
        i = j
    return [seg for k, seg in enumerate(out)
            if not _is_isolated_stock_phrase(out, k, stock_isolation_sec)]
