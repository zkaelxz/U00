"""
misheard_finder.py -- find Chinese words in a title's source lines that sound like a trusted
word the transcript spells differently (ASR heard a similar character).

Pure and offline: no database, no network, no AI. The caller supplies the lines, the trusted
words (glossary, names, title) and a word cutter. Nothing here changes a line; the result is a
list of "maybe this was X" suggestions for a person to confirm.

Only same-length words are compared: a mis-heard character swaps one syllable for a similar
one, it does not add or drop a character, and that rule keeps the noise down. Sounds come from
glossary_phonetic (pypinyin); a missing library surfaces as PhoneticUnavailable.
"""
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import glossary_phonetic as ph

# --- Tunables (the one place for the numbers) -------------------------------
MIN_WORD_LEN = 2          # a single character is too common to call mis-heard
MAX_WORD_LEN = 6          # longer than any name or term; keeps comparisons cheap
RARE_MAX_COUNT = 2        # a word heard this often or less can be the slip
FREQUENT_MIN_COUNT = 3    # a word heard this often is trusted: ASR rarely errs the same way thrice
# Real test titles: two-character names matched on a different sound, and words of the title or
# heard often matched to ordinary words, gave nearly all the noise and none of the real slips.
EVERYDAY_SOURCE_MIN_LEN = 4  # title / frequent words shorter than this are ordinary speech
# Exclamations and sentence particles: a window that starts or ends on one (哎唷洛赫啊 gives
# 唷洛赫) is a real name next to an interjection, not a name heard wrongly.
INTERJECTION_CHARS = frozenset("哎唷唉啊呀喔哦噢嗯欸哇咦哈嘿")
MAX_SUGGESTIONS = 30      # more than a person reads in one sitting; the best come first
MAX_LINES_SHOWN = 5       # enough to find the word in the episode
MAX_COMPARISONS = 200_000  # one title's share; a long title stops early and says so

# Lower is more trusted: a glossary term was typed by a person, a frequent word is only a guess.
SOURCE_RANK = {"glossary": 0, "name": 1, "title": 2, "frequent": 3}

Cutter = Callable[[str], List[str]]
Canon = Callable[[str], str]
# (line index, line id, text)
SourceLine = Tuple[int, Optional[int], str]


def _is_word(word: str) -> bool:
    return (MIN_WORD_LEN <= len(word) <= MAX_WORD_LEN
            and all(ph.char_script(c) == "han" for c in word))


Spots = List[Tuple[int, Optional[int]]]


def _count_words(lines, cut: Cutter, canon: Canon):
    seen: Dict[str, Spots] = defaultdict(list)
    surface: Dict[str, Counter] = defaultdict(Counter)
    for idx, line_id, text in lines:
        for word in cut(text):
            if _is_word(word):
                key = canon(word)
                seen[key].append((idx, line_id))
                surface[key][word] += 1
    return seen, surface


def _trusted_pool(trusted, seen, surface, canon: Canon,
                  display: Canon = lambda w: w) -> Dict[str, Tuple[str, str]]:
    """canon word -> (source, display text); a person-typed source beats a frequent one.
    A typed word is shown through `display` (a Simplified glossary term on a Traditional title)."""
    pool: Dict[str, Tuple[str, str]] = {}
    for word, source in trusted:
        word = (word or "").strip()
        if _is_word(word):
            key = canon(word)
            if key not in pool or SOURCE_RANK[source] < SOURCE_RANK[pool[key][0]]:
                pool[key] = (source, display(word))
    for key, spots in seen.items():
        if len(spots) >= FREQUENT_MIN_COUNT and key not in pool:
            pool[key] = ("frequent", surface[key].most_common(1)[0][0])
    return pool


def _character_windows(lines, pool, surface, canon: Canon) -> Dict[str, Spots]:
    """A name the cutter doesn't know is split at arbitrary places, so a mis-heard name is
    rarely one cut word. Names and glossary terms are therefore also looked for as plain
    character windows of their own length."""
    windows: Dict[str, Spots] = defaultdict(list)
    sizes = {len(k) for k, (src, _) in pool.items() if src != "frequent"}
    for idx, line_id, text in lines:
        for size in sizes:
            for i in range(len(text) - size + 1):
                piece = text[i:i + size]
                if all(ph.char_script(c) == "han" for c in piece) and canon(piece) not in pool:
                    windows[canon(piece)].append((idx, line_id))
                    surface[canon(piece)][piece] += 1
    return windows


def _one_per_stretch(found: List[dict]) -> List[dict]:
    """Overlapping windows can all point at one stretch; the best-ranked one is enough."""
    kept: List[dict] = []
    for item in found:
        first = item["lines"][0]["idx"]
        if not any(k["lines"][0]["idx"] == first and set(k["heard"]) & set(item["heard"])
                   for k in kept):
            kept.append(item)
    return kept


def find_misheard(lines: Iterable[SourceLine], trusted: Iterable[Tuple[str, str]], cut: Cutter,
                  canon: Canon = lambda w: w,
                  display: Canon = lambda w: w,
                  is_dictionary_word: Callable[[str], bool] = lambda w: False,
                  skip: Iterable[Tuple[str, str]] = ()) -> dict:
    """Suggestions for words that sound like a trusted word spelled differently.

    `trusted` is (word, source) with source one of SOURCE_RANK's glossary / name / title.
    `canon` folds Traditional to Simplified so a pair of forms counts as one word. `display`
    writes a typed word in the title's own script, since a suggestion is what Use here writes.
    `is_dictionary_word` says a word is ordinary Chinese: such a word is never offered as a
    slip of a merely frequent word (地板 for 地上), only of a glossary, name or title word.
    `skip` is (heard, suggested) pairs a person answered Keep as heard: dropped before the cap,
    so they never use up a slot.
    Returns {"suggestions": [...], "truncated": bool}; each suggestion carries the heard
    spelling, the suggested one, where the suggestion came from, both counts and the lines."""
    lines = list(lines)
    seen, surface = _count_words(lines, cut, canon)
    pool = _trusted_pool(trusted, seen, surface, canon, display)
    by_length: Dict[int, List[Tuple[str, str, str, str]]] = defaultdict(list)
    for key, (source, display) in pool.items():
        by_length[len(key)].append((key, display, source, ph.phonetic_key(key, "zh")))
    # A spelling inside a longer glossary or name spelling (an alias) is that name, already known.
    inside_known = {k[i:j] for k, (src, _) in pool.items() if src in ("glossary", "name")
                    for i in range(len(k)) for j in range(i + MIN_WORD_LEN, len(k) + 1)
                    if j - i < len(k)}
    windows = _character_windows(lines, pool, surface, canon)
    # Windows only answer to person-typed words: matching them to merely frequent words
    # would flag every stretch of ordinary speech.
    candidates = [(k, v, True) for k, v in seen.items()]
    candidates += [(k, v, False) for k, v in windows.items() if k not in seen]

    found: List[dict] = []
    checks = 0
    truncated = False
    for heard, spots, allow_frequent in candidates:
        if heard in pool or len(spots) > RARE_MAX_COUNT:
            continue
        if heard in inside_known or heard[0] in INTERJECTION_CHARS or heard[-1] in INTERJECTION_CHARS:
            continue
        heard_sound = ph.phonetic_key(heard, "zh")
        best = None
        for key, display, source, sound in by_length.get(len(heard), ()):
            if source == "frequent" and not allow_frequent:
                continue
            checks += 1
            if checks > MAX_COMPARISONS:
                truncated = True
                break
            if not ph.similar(heard_sound, sound, "zh"):
                continue
            if source in ("frequent", "title") and (
                    len(heard) < EVERYDAY_SOURCE_MIN_LEN or is_dictionary_word(heard)):
                continue
            # Two characters have too little sound to compare loosely: only the same sound
            # (子/直) is a slip, anything else is another word.
            if len(heard) == 2 and heard_sound != sound:
                continue
            # More than a third of the characters changed (at least one) is a different word.
            if sum(a != b for a, b in zip(heard, key)) > max(1, len(heard) // 3):
                continue
            rank = (SOURCE_RANK[source], ph.key_distance(heard_sound, sound),
                    -len(seen.get(key, ())))
            if best is None or rank < best[0]:
                best = (rank, key, display, source)
        if best:
            rank, key, display, source = best
            found.append({"heard": surface[heard].most_common(1)[0][0], "suggested": display,
                          "source": source, "heard_count": len(spots),
                          "suggested_count": len(seen.get(key, ())),
                          "lines": [{"idx": i, "line_id": lid} for i, lid in spots[:MAX_LINES_SHOWN]],
                          "_rank": rank})
        if truncated:
            break
    skipped = set(skip)
    found = [f for f in found if (f["heard"], f["suggested"]) not in skipped]
    found.sort(key=lambda s: (s["_rank"], s["heard"]))
    found = _one_per_stretch(found)
    for item in found:
        del item["_rank"]
    return {"suggestions": found[:MAX_SUGGESTIONS], "truncated": truncated or len(found) > MAX_SUGGESTIONS}
