"""Flags translated lines whose English does not match their source, by comparing multilingual embeddings."""
import json
import logging
import math

from lib import http

from .shared import PROVIDER_RESPONSE_MAX_BYTES, redact_secrets

log = logging.getLogger(__name__)

ALIGNMENT_FLAG = "source_mismatch"
# subtitle_formats.READING_SPEED_FLAG, which a test pins; nothing in engine_backends imports that module.
READING_SPEED_FLAG = "reading_speed"
FLAG_NOTE_PREFIX = "Source match: "
EMBED_MODEL = "bge-m3"

# Chosen from a 14-pair zh/ja/ko hand check where right pairs scored 0.79-0.95 and
# another line's English at most 0.68. Real series have not been measured, so the
# bar sits between those and a flag only asks for a look.
MIN_SIMILARITY = 0.55
# A one- or two-character line ("はい", "嗯") has too little for an embedding to compare.
MIN_SOURCE_CHARS = 3
EMBED_BATCH = 32
# The first request can load the model into memory, so it gets longer than a chat tick.
EMBED_TIMEOUT = 120
# A switched-off PC at the Ollama address must not hold Finish for the whole read timeout.
EMBED_CONNECT_TIMEOUT = 5


# Ollama 0.24 answers HTTP 500 "unsupported value: NaN" for bge-m3 on a text of about 60
# characters or more, and one such text fails its whole request. A text that fails alone is
# embedded in short pieces and the vectors averaged, which keeps the line checkable; it only
# runs for texts the whole-text request could not do, so a fixed Ollama never reaches it.
PIECE_CHARS = 40
_PIECE_BREAKS = "。！？!?.；;，,、 \n"


def _post_embed(base_url: str, model: str, chunk: list):
    """(status, vectors) for one /api/embed request; raises http.FetchError or ValueError."""
    resp = http.post(f"{base_url}/api/embed", timeout=(EMBED_CONNECT_TIMEOUT, EMBED_TIMEOUT),
                     max_bytes=PROVIDER_RESPONSE_MAX_BYTES, guard=None,
                     deadline=2 * EMBED_TIMEOUT, json={"model": model, "input": chunk})
    if not resp.ok:
        return resp.status, None
    got = json.loads(resp.body).get("embeddings")
    return resp.status, got if isinstance(got, list) and len(got) == len(chunk) else None


def _pieces(text: str) -> list:
    """The text cut into runs of at most PIECE_CHARS characters, at a break mark where it can."""
    out, rest = [], text.strip()
    while rest:
        if len(rest) <= PIECE_CHARS:
            out.append(rest)
            break
        cut = max((rest.rfind(c, 0, PIECE_CHARS) for c in _PIECE_BREAKS), default=-1) + 1
        cut = cut if cut >= PIECE_CHARS // 2 else PIECE_CHARS
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [p for p in out if p]


def _embed_one(base_url: str, model: str, text: str):
    """One text's vector, or None. A text the server cannot take whole is embedded in
    pieces and averaged."""
    status, got = _post_embed(base_url, model, [text])
    if got:
        return got[0]
    pieces = _pieces(text)
    if status != 500 or len(pieces) < 2:
        return None
    status, got = _post_embed(base_url, model, pieces)
    if not got:
        return None
    return [sum(v[i] for v in got) / len(got) for i in range(len(got[0]))]


def _embed_around_nan(base_url: str, model: str, chunk: list) -> list:
    """Vectors for a batch the server refused with a 500, in a handful of requests rather
    than one per text: the refusal comes from its longest texts (the NaN answer above
    starts at a length), so the shortest texts that still go through together are found
    by halving. Each longer text then gets _embed_one, as before: the server takes some
    of them whole when they are sent alone, and a whole text's vector is not its pieces'
    average (up to 0.14 apart on a real stream), so the result stays what it was."""
    order = sorted(range(len(chunk)), key=lambda i: len(chunk[i]))
    # Every text together failed, so at most all but the longest can go through.
    fits, fitted, too_many = 0, [], len(chunk)
    while too_many - fits > 1:
        mid = (fits + too_many) // 2
        _status, got = _post_embed(base_url, model, [chunk[i] for i in order[:mid]])
        if got:
            fits, fitted = mid, got
        else:
            too_many = mid
    vectors: list = [None] * len(chunk)
    for i, vector in zip(order[:fits], fitted):
        vectors[i] = vector
    for i in order[fits:]:
        vectors[i] = _embed_one(base_url, model, chunk[i])
    return vectors


def embed(base_url: str, texts: list, model: str = EMBED_MODEL, should_stop=None, on_progress=None):
    """One vector per text from Ollama's /api/embed (None in place of a text the server
    could not embed), or None when the server or the model is unusable, or `should_stop`
    turns true between requests (a stopped run). A missing model must never fail the
    translate run. on_progress(texts done, texts) is called before each request."""
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH):
        if should_stop and should_stop():
            return None
        if on_progress:
            on_progress(start, len(texts))
        chunk = texts[start:start + EMBED_BATCH]
        try:
            status, got = _post_embed(base_url, model, chunk)
            if got is None and status == 500:
                got = _embed_around_nan(base_url, model, chunk)
            elif got is None:
                log.warning("Source-match check skipped: Ollama returned HTTP %s for %s "
                            "(is it pulled? `ollama pull %s`)", status, model, model)
                return None
        except (http.FetchError, ValueError) as exc:
            log.warning("Source-match check skipped: %s", redact_secrets(str(exc)))
            return None
        vectors.extend(got)
    return vectors


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def flag_note(score: float) -> str:
    return f"{FLAG_NOTE_PREFIX}the English reads unlike its source line (similarity {score:.2f})"


def shift_note(which: str, own: float, other: float) -> str:
    return (f"{FLAG_NOTE_PREFIX}the English fits the {which} line's source better "
            f"({other:.2f}) than its own ({own:.2f})")


# A long line's English can score above MIN_SIMILARITY against a neighbouring line's source,
# because nearby lines of a scene talk about the same thing, so a shifted reply passes the bar.
# Its own source should still score clearly higher than a neighbour's: when a neighbour's source
# beats it by this much, the English has probably slid onto the wrong line.
NEIGHBOUR_MARGIN = 0.08


def _closer_neighbour(i: int, pos, todo, vectors, score: float, margin: float):
    """("previous" or "next", its similarity) when the line's English is nearer the source of the
    line just before or after it by `margin`, else None. Only a neighbour that is the very next
    entry in `lines` and has vectors counts."""
    n = len(todo)
    best = None
    for step, which in ((-1, "previous"), (1, "next")):
        j = i + step
        if not 0 <= j < n or vectors[j] is None:
            continue
        if pos[id(todo[j])] - pos[id(todo[i])] != step:
            continue
        other = cosine(vectors[j], vectors[n + i])
        if other >= score + margin and (best is None or other > best[1]):
            best = (which, other)
    return best


# With a judge to confirm each doubt, the neighbour test can afford to be looser: most of
# what it catches extra is then cleared by the judge instead of flagged.
CANDIDATE_MARGIN = 0.03
JUDGED_NOTES = {
    "shifted": FLAG_NOTE_PREFIX + "a check found this English translates a neighbouring line, not its own",
    "wrong": FLAG_NOTE_PREFIX + "a check found this English does not say what its source line says",
}


def _judge_item(i: int, ln, todo, pos) -> dict:
    """The line and the directly adjacent lines on either side, for a judge's prompt."""
    item = {"id": i, "zh": ln.zh, "en": ln.en}
    for step, key in ((-1, "prev"), (1, "next")):
        j = i + step
        if 0 <= j < len(todo) and pos[id(todo[j])] - pos[id(ln)] == step:
            item[key] = (todo[j].zh, todo[j].en)
    return item


def flag_mismatches(lines, embed_texts, min_similarity: float = MIN_SIMILARITY,
                    unchecked=None, neighbour_margin: float | None = NEIGHBOUR_MARGIN,
                    judge=None) -> int:
    """Flags lines whose English is far from their source, or nearer a neighbouring line's
    source than its own (None for `neighbour_margin` turns that second test off); returns how
    many it flagged.

    embed_texts(list of str) returns one vector per text (None for a text it could not
    embed), or None when embedding is unavailable, which leaves every line as it was. A line
    that already carries another flag keeps it; one only this check flagged earlier is
    cleared once it passes. A line with no vector is left alone and added to `unchecked`.

    judge, if given, takes the doubtful lines (found with the looser CANDIDATE_MARGIN) as
    {id, zh, en, prev, next} items and returns {id: "ok" | "shifted" | "wrong"}; only a
    "shifted" or "wrong" line is flagged. A line it gives no verdict for is decided by the
    embedding scores alone, as without a judge."""
    lines = list(lines)
    todo = [ln for ln in lines
            if (ln.en or "").strip() and len((ln.zh or "").strip()) >= MIN_SOURCE_CHARS]
    if not todo:
        return 0
    vectors = embed_texts([ln.zh for ln in todo] + [ln.en for ln in todo])
    if vectors is None or len(vectors) != 2 * len(todo):
        return 0
    pos = {id(ln): k for k, ln in enumerate(lines)}
    strict, doubtful, borderline, strong = {}, [], {}, {}
    for i, ln in enumerate(todo):
        if vectors[i] is None or vectors[len(todo) + i] is None:
            if unchecked is not None:
                unchecked.append(ln)
            continue
        score = cosine(vectors[i], vectors[len(todo) + i])
        note = flag_note(score) if score < min_similarity else None
        near = None
        if neighbour_margin is not None:
            near = _closer_neighbour(i, pos, todo, vectors, score, neighbour_margin)
            note = note or (shift_note(near[0], score, near[1]) if near else None)
        strict[i] = note
        borderline[i] = score >= min_similarity
        strong[i] = not borderline[i] or bool(
            near and _closer_neighbour(i, pos, todo, vectors, score,
                                       max(neighbour_margin, READING_SPEED_MARGIN)))
        # A line under the bar stays flagged whatever the judge says, so asking would only
        # pay for a different note.
        if (judge is not None and score >= min_similarity and neighbour_margin is not None
                and _closer_neighbour(i, pos, todo, vectors, score, CANDIDATE_MARGIN)):
            doubtful.append(i)
    verdicts = (judge([_judge_item(i, todo[i], todo, pos) for i in doubtful])
                if judge is not None and doubtful else {})
    flagged = hidden = 0
    for i, note in strict.items():
        ln = todo[i]
        if i in verdicts and (verdicts[i] != "ok" or borderline.get(i)):
            # "ok" only overrules a borderline neighbour doubt: a score under the bar stays
            # flagged, since a model that says "ok" to everything must not empty the check.
            note = JUDGED_NOTES.get(verdicts[i])
            strong[i] = True
        if note and _set_flag(ln, note, strong[i]):
            flagged += 1
        elif note:
            hidden += 1
        else:
            _clear_flag(ln)
    if hidden:
        # How often the one flag slot still hides a doubt decides whether more flags may give way.
        log.info("source-match check: %d doubted lines kept another flag", hidden)
    return flagged


# Reading speed is set on every line before this check runs, so keeping the slot for it would
# leave exactly the long, swallowed-a-neighbour lines unchecked. Its note is kept after this
# tail, so a later clean check can give the flag back.
READING_SPEED_TAIL = " Also (too fast to read): "
# Every doubt shown on a reading speed line is a flag the user did not get before, so there the
# neighbour test needs the wider margin (on the gold pairs 0.12 gave 0.4-1.4% false flags against
# 1.3-2.8% at 0.08), unless the score is under the bar or a judge confirmed the doubt.
READING_SPEED_MARGIN = 0.12


def _set_flag(ln, note: str, strong: bool) -> bool:
    """True when the line now carries the doubt as its flag; another flag keeps its slot, and a
    reading speed flag gives way only to a `strong` doubt (READING_SPEED_MARGIN)."""
    if ln.flag == ALIGNMENT_FLAG:
        _own, sep, old = (ln.flag_note or "").partition(READING_SPEED_TAIL)
        ln.flag_note = note + (sep + old if sep else "")
    elif ln.flag == READING_SPEED_FLAG and strong:
        ln.flag, ln.flag_note = ALIGNMENT_FLAG, note + READING_SPEED_TAIL + (ln.flag_note or "")
    elif not ln.flag:
        ln.flag, ln.flag_note = ALIGNMENT_FLAG, note
    else:
        return False
    return True


def drop_displaced_reading_speed(ln) -> bool:
    """Forgets a reading speed flag this check took the place of, so clearing reading speed flags
    clears it too and a later clean check cannot bring it back; True when there was one."""
    if ln.flag != ALIGNMENT_FLAG or READING_SPEED_TAIL not in (ln.flag_note or ""):
        return False
    ln.flag_note = ln.flag_note.partition(READING_SPEED_TAIL)[0]
    return True


def _clear_flag(ln) -> None:
    """Takes back a doubt the English no longer earns, giving a displaced reading speed flag back."""
    if ln.flag == ALIGNMENT_FLAG:
        _own, sep, old = (ln.flag_note or "").partition(READING_SPEED_TAIL)
        ln.flag, ln.flag_note = (READING_SPEED_FLAG, old) if sep else (None, "")
