"""Translate-by-sentence helpers: group timed fragments that form one spoken
sentence, show the model the whole sentence, and put its English back onto
the individual fragments. Pure functions over Line-like objects and strings;
the request/retry loop lives in engine_backends/shared.py and the run loop in
engine_backends/translate_pipeline.py.
"""
import re

# Transcription splits a sentence at word pauses, so fragments of one sentence
# are close together. It must stay below the pipeline's scene-break gap (3 s)
# so a group can never straddle a scene break.
MAX_GAP_SECONDS = 1.0
MAX_GROUP_LINES = 4
MAX_GROUP_CHARS = 60
MAX_GROUP_SECONDS = 12.0

# Closing quotes count as final: a quoted utterance has ended even when the
# punctuation before it is a comma.
_SENTENCE_FINAL = "。！？!?….．｡‥”’」』\"'"
_TRAILING_CLOSERS = ")）]】》"

_CJK = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿＀-￯]")

SENTENCE_NOTE = (
    "Some lines below are one spoken sentence that was cut into short timed parts: a "
    "\"Sentence in N parts\" header gives the whole sentence, followed by its numbered "
    "parts. Translate the whole sentence naturally first, then split the English across "
    "that sentence's part numbers in order, so each part reads as a natural continuation "
    "and works as its own subtitle. Every part number must get a non-empty translation "
    "(a part may be one word or a short phrase); never repeat the whole sentence in each "
    "part and never put it all in one part. Lines without a header are translated as "
    "usual.\n\n")


def is_sentence_final(text: str) -> bool:
    stripped = (text or "").rstrip().rstrip(_TRAILING_CLOSERS).rstrip()
    return bool(stripped) and stripped[-1] in _SENTENCE_FINAL


def _continues(prev, nxt) -> bool:
    """Whether `nxt` reads as the rest of the sentence `prev` was cut from."""
    if not (prev.zh or "").strip() or not (nxt.zh or "").strip():
        return False
    if prev.flag or nxt.flag or getattr(prev, "sfx", False) or getattr(nxt, "sfx", False):
        return False
    if prev.speaker != nxt.speaker or getattr(prev, "lang", None) != getattr(nxt, "lang", None):
        return False
    if is_sentence_final(prev.zh):
        return False
    return 0 <= nxt.start - prev.start and nxt.start - prev.end <= MAX_GAP_SECONDS


def join_fragments(parts: list) -> str:
    """The sentence text: no space between CJK pieces, one between
    space-separated scripts (Korean words, Latin)."""
    out = ""
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if out and not (_CJK.match(out[-1]) or _CJK.match(part[0])):
            out += " "
        out += part
    return out


def group_fragments(target_lines: list, all_lines: list) -> list:
    """Splits `target_lines` into consecutive groups (lists of Lines); a
    one-line group is a line to translate as usual. Only lines that are
    neighbours in `all_lines` can share a group, so an already-translated
    line between two targets is never swallowed or overwritten."""
    position = {ln.idx: i for i, ln in enumerate(all_lines)}
    groups = []
    for ln in target_lines:
        cur = groups[-1] if groups else None
        if (cur and position.get(ln.idx) == position.get(cur[-1].idx, -2) + 1
                and _continues(cur[-1], ln)
                and len(cur) < MAX_GROUP_LINES
                and sum(len(x.zh.strip()) for x in cur) + len(ln.zh.strip()) <= MAX_GROUP_CHARS
                and ln.end - cur[0].start <= MAX_GROUP_SECONDS):
            cur.append(ln)
        else:
            groups.append([ln])
    return groups


def align_batches_to_groups(batches: list, groups: list) -> list:
    """Moves the head of a batch into the previous one when a group would
    otherwise be cut by the batch boundary (a batch may then run a few lines
    over its size). Drops a batch that ends up empty."""
    group_of = {id(ln): gi for gi, g in enumerate(groups) for ln in g}
    out = [list(b) for b in batches]
    for i in range(len(out) - 1):
        while (out[i] and out[i + 1]
               and group_of[id(out[i][-1])] == group_of[id(out[i + 1][0])]):
            out[i].append(out[i + 1].pop(0))
    return [b for b in out if b]


def complete_groups(chunk: list, groups: list) -> list:
    """The multi-line groups lying wholly inside `chunk` -- a bisected or
    shortened chunk can hold only part of a sentence, which is then just
    translated line by line."""
    members = {id(ln) for ln in chunk}
    return [g for g in groups if len(g) > 1 and all(id(ln) in members for ln in g)]


def render_grouped(ids: list, line_text: dict, groups_by_id: dict, zh_by_id: dict) -> str:
    """The numbered block for `ids` with a "Sentence in N parts" header ahead
    of each group's lines. line_text maps id -> the usual "id. [name] text"
    line; groups_by_id maps an id to its group's ids (ungrouped ids are
    absent). Starts with SENTENCE_NOTE when any group is shown."""
    out, shown, any_group = [], set(), False
    for i in ids:
        group = groups_by_id.get(i)
        if group is None:
            out.append(line_text[i])
            continue
        if group[0] not in shown:
            shown.add(group[0])
            any_group = True
            out.append(f"Sentence in {len(group)} parts: "
                       + join_fragments([zh_by_id[g] for g in group]))
        out.append(line_text[i])
    return (SENTENCE_NOTE if any_group else "") + "\n".join(out)


def _split_points(weights: list, n_words: int) -> list:
    n = len(weights)
    total = sum(weights) or n
    points, acc, prev = [], 0, 0
    for k in range(n - 1):
        acc += weights[k] or 1
        cut = round(n_words * acc / total)
        points.append(min(max(cut, prev + 1), n_words - (n - 1 - k)))
        prev = points[-1]
    return points


def split_translation(text: str, weights: list):
    """Splits one translated sentence into len(weights) non-empty pieces at
    word boundaries, each about as long as its fragment's source text, or
    None when there are fewer words than fragments. A cut within one word of
    the ideal spot is moved to right after a comma or similar, so pieces
    break at a clause where there is one."""
    words = (text or "").split()
    n = len(weights)
    if n < 2 or len(words) < n:
        return None
    points, prev = [], 0
    for k, cut in enumerate(_split_points(weights, len(words))):
        low, high = prev + 1, len(words) - (n - 1 - k)
        best = cut
        for cand in (cut, cut - 1, cut + 1):
            if low <= cand <= high and re.search(r"[,;:.!?，、]$", words[cand - 1]):
                best = cand
                break
        points.append(best)
        prev = best
    bounds = [0] + points + [len(words)]
    return [" ".join(words[a:b]) for a, b in zip(bounds, bounds[1:])]


def settle_group(zh_parts: list, pieces: list):
    """The English for each fragment of one group given what the model
    returned per fragment id: the pieces as they are when each is non-empty
    and they differ, a deterministic split when the model put the whole
    sentence in one piece or repeated it in every piece, else None (the
    caller then translates the fragments the normal way)."""
    texts = [(p or "").strip() for p in pieces]
    filled = [t for t in texts if t]
    if not filled:
        return None
    if len(filled) == len(texts) and (len(set(filled)) > 1 or len(set(zh_parts)) == 1):
        return texts
    if len(set(filled)) == 1:
        return split_translation(filled[0], [len(z.strip()) for z in zh_parts])
    return None
