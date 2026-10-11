"""Post-checks on a translated batch for the ways an id-keyed reply can be
complete and still wrong: two lines merged into one reply, text shifted onto a
neighbouring id, or source script left in the English. Pure functions; the
retry and flagging live in translate_pipeline."""

import math
import re

# Thresholds are deliberately generous: a false alarm costs a paid re-ask, a
# miss only leaves today's behaviour.
WORDS_PER_CJK_CHAR = 0.5       # subtitle English runs about half a word per CJK character
LONG_FACTOR = 2.5              # a reply this many times the expected words is probably two lines merged
LONG_SLACK_WORDS = 6           # compact source lines legitimately expand a lot in absolute terms
SHORT_FACTOR = 0.25            # a reply this small against a long source probably lost its text to a neighbour
SHORT_MIN_EXPECTED = 6         # short sources ("嗯") have no meaningful minimum length
SOFT_LONG_FACTOR = 1.8         # looser bounds, used only beside a line that already looks wrong
SOFT_SHORT_FACTOR = 0.4
SOFT_MIN_EXPECTED = 4
DUPLICATE_MIN_WORDS = 5        # "There was no gun." is a legitimate reply to near-identical sources (measured: 4 words flagged 26% of ja batches)
CONTAINED_MIN_WORDS = 6        # a short reply inside a longer one ("He's Red John." in a question about him) is normal dialogue
SWAP_MIN_EXPECTED = 6          # swap-fit needs lines long enough for length to tell them apart
SWAP_IMPROVEMENT = 0.5         # swapped replies must fit at least twice as well to count as shifted

FLAG_NOTE = "check: may be merged or shifted"

_CJK = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯ᄀ-ᇿ]")
_CJK_RUN = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯ᄀ-ᇿ]+")
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”|「[^」]*」|『[^』]*』')
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _english_words(text: str) -> int:
    return len(_WORD.findall(text))


def _expected_words(source: str) -> float:
    """English words a source line should come to: CJK characters at a fixed
    rate plus any Latin words already in the source (names, numbers)."""
    return len(_CJK.findall(source)) * WORDS_PER_CJK_CHAR + len(_WORD.findall(_CJK.sub(" ", source)))


def _norm(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", text.casefold()).split())


def allowed_source_runs(glossary_terms=None, style_guidelines: str = "") -> set:
    """Source-script runs the title deliberately keeps in the English: any run
    in a glossary term or in the style guidelines (which carry the glossary)."""
    runs = set(_CJK_RUN.findall(style_guidelines or ""))
    for term in glossary_terms or []:
        for key in ("term_original", "term_translation"):
            runs.update(_CJK_RUN.findall(term.get(key) or ""))
    return runs


def leftover_source_script(reply: str, allowed_runs=()) -> bool:
    """Source-script text left in the English, outside quotes and kept terms."""
    text = _QUOTED.sub(" ", reply)
    for run in sorted(allowed_runs, key=len, reverse=True):
        text = text.replace(run, " ")
    return bool(_CJK.search(text))


def length_issue(source: str, reply: str, soft: bool = False):
    """'long' or 'short' when the reply's length is implausible for the source."""
    expected = _expected_words(source)
    words = _english_words(reply)
    if soft:
        long_factor, short_factor, min_expected = SOFT_LONG_FACTOR, SOFT_SHORT_FACTOR, SOFT_MIN_EXPECTED
    else:
        long_factor, short_factor, min_expected = LONG_FACTOR, SHORT_FACTOR, SHORT_MIN_EXPECTED
    if expected >= min_expected and words > long_factor * expected + LONG_SLACK_WORDS:
        return "long"
    if expected >= min_expected and words < short_factor * expected:
        return "short"
    return None


def _fit_error(source: str, reply: str) -> float:
    return abs(math.log((_english_words(reply) + 1) / (_expected_words(source) + 1)))


def duplicate_positions(sources: list, replies: list) -> set:
    """Positions whose reply is identical to, or contained in, the reply of a
    line with a different source. Both sides are reported: either may be the
    one that took the other's text."""
    norm = [_norm(r) for r in replies]
    found = set()
    for i in range(len(replies)):
        if len(norm[i].split()) < DUPLICATE_MIN_WORDS:
            continue
        for j in range(len(replies)):
            if i == j or sources[i] == sources[j] or not norm[j]:
                continue
            # A source that contains the other legitimately yields a reply that does.
            if sources[i] in sources[j] or sources[j] in sources[i]:
                continue
            contained = (len(norm[j]) > len(norm[i]) and len(norm[i].split()) >= CONTAINED_MIN_WORDS
                         and f" {norm[i]} " in f" {norm[j]} ")
            if norm[i] == norm[j] or contained:
                found.update((i, j))
    return found


def line_problems(source: str, reply: str, allowed_runs=()) -> list:
    """Problems visible from one line alone."""
    problems = []
    if not reply.strip():
        return problems
    issue = length_issue(source, reply)
    if issue:
        problems.append(issue)
    if leftover_source_script(reply, allowed_runs):
        problems.append("leftover")
    return problems


def suspect_positions(sources: list, replies: list, allowed_runs=()) -> dict:
    """{position: reason} for replies in this batch that look merged, shifted or
    untranslated. Positions are indexes into the two parallel lists; an empty
    reply is never suspect here (a missing translation is reported elsewhere)."""
    suspects = {}
    for i, (src, rep) in enumerate(zip(sources, replies)):
        problems = line_problems(src, rep, allowed_runs)
        if problems:
            suspects[i] = problems[0]
    for i in duplicate_positions(sources, [r if r.strip() else "" for r in replies]):
        suspects.setdefault(i, "duplicate")

    # Shift pattern: a line that is only mildly off, next to one that is clearly
    # off, is the other half of the same displaced text.
    hard = set(suspects)
    for i in range(len(replies)):
        if i in suspects or not replies[i].strip():
            continue
        beside = (i - 1 in hard) or (i + 1 in hard)
        if beside and length_issue(sources[i], replies[i], soft=True):
            suspects[i] = "shift"

    # Neighbours whose replies fit each other's sources better than their own.
    for i in range(len(replies) - 1):
        a, b = i, i + 1
        if not (replies[a].strip() and replies[b].strip()):
            continue
        if min(_expected_words(sources[a]), _expected_words(sources[b])) < SWAP_MIN_EXPECTED:
            continue
        own = _fit_error(sources[a], replies[a]) + _fit_error(sources[b], replies[b])
        swapped = _fit_error(sources[a], replies[b]) + _fit_error(sources[b], replies[a])
        if own > 0.8 and swapped < own * SWAP_IMPROVEMENT:
            suspects.setdefault(a, "shift")
            suspects.setdefault(b, "shift")
    return suspects


def guard_batch(chunk: list, redo, stop, allowed_runs=()) -> list:
    """Checks a translated batch (Lines whose .en is set) and re-asks only the
    suspect ones, in chunks of at most half the batch, so one batch costs at
    most two extra requests and the re-ask is never itself re-checked or looped.

    redo(lines) -> one translation per line in the same order (the engine
    matches them to the lines by id), or None if the request failed; stop() ->
    True when the cost cap, a cancel or an earlier failure says no more
    requests. A re-ask replaces a reply only when it fails fewer checks.

    Returns [(line, reason)] for lines still suspect afterwards; the caller
    flags them."""
    live = [ln for ln in chunk if (ln.en or "").strip()]
    sources = [ln.zh for ln in live]
    replies = [ln.en for ln in live]
    suspects = suspect_positions(sources, replies, allowed_runs)
    if not suspects:
        return []
    half = max(1, math.ceil(len(chunk) / 2))
    positions = sorted(suspects)
    for start in range(0, len(positions), half):
        if stop():
            break
        part = positions[start:start + half]
        redone = redo([live[p] for p in part])
        if redone is None:
            break
        if len(redone) != len(part):
            continue
        # Judged together: a swapped pair only reads as fixed once both halves are in.
        candidate = list(replies)
        for p, new in zip(part, redone):
            if (new or "").strip():
                candidate[p] = new
        still = suspect_positions(sources, candidate, allowed_runs)
        for p in part:
            if candidate[p] is replies[p]:
                continue
            if p not in still or len(line_problems(sources[p], candidate[p], allowed_runs)) < len(
                    line_problems(sources[p], replies[p], allowed_runs)):
                replies[p] = candidate[p]
                live[p].en = candidate[p]
    return [(live[p], reason) for p, reason in suspect_positions(sources, replies, allowed_runs).items()]
