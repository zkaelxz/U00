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


def find_misheard(lines: Iterable[SourceLine], trusted: Iterable[Tuple[str, str]], cut: Cutter,
                  canon: Canon = lambda w: w) -> dict:
    """Suggestions for words that sound like a trusted word spelled differently.

    `trusted` is (word, source) with source one of SOURCE_RANK's glossary / name / title.
    `canon` folds Traditional to Simplified so a pair of forms counts as one word.
    Returns {"suggestions": [...], "truncated": bool}; each suggestion carries the heard
    spelling, the suggested one, where the suggestion came from, both counts and the lines."""
    seen: Dict[str, List[Tuple[int, Optional[int]]]] = defaultdict(list)
    surface: Dict[str, Counter] = defaultdict(Counter)
    for idx, line_id, text in lines:
        for word in cut(text):
            if _is_word(word):
                key = canon(word)
                seen[key].append((idx, line_id))
                surface[key][word] += 1

    def shown(key: str) -> str:
        return surface[key].most_common(1)[0][0]

    pool: Dict[str, Tuple[str, str]] = {}  # canon word -> (source, display text)
    for word, source in trusted:
        word = (word or "").strip()
        if _is_word(word):
            key = canon(word)
            if key not in pool or SOURCE_RANK[source] < SOURCE_RANK[pool[key][0]]:
                pool[key] = (source, word)
    for key, spots in seen.items():
        if len(spots) >= FREQUENT_MIN_COUNT and key not in pool:
            pool[key] = ("frequent", shown(key))

    by_length: Dict[int, List[Tuple[str, str, str, str]]] = defaultdict(list)
    for key, (source, display) in pool.items():
        by_length[len(key)].append((key, display, source, ph.phonetic_key(key, "zh")))

    found: List[dict] = []
    checks = 0
    truncated = False
    for heard, spots in seen.items():
        if heard in pool or len(spots) > RARE_MAX_COUNT:
            continue
        heard_sound = ph.phonetic_key(heard, "zh")
        best = None
        for key, display, source, sound in by_length.get(len(heard), ()):
            checks += 1
            if checks > MAX_COMPARISONS:
                truncated = True
                break
            if not ph.similar(heard_sound, sound, "zh"):
                continue
            # More than half the characters changed is a different word, not one slip.
            if sum(a != b for a, b in zip(heard, key)) > (len(heard) + 1) // 2:
                continue
            rank = (SOURCE_RANK[source], ph.key_distance(heard_sound, sound),
                    -len(seen.get(key, ())))
            if best is None or rank < best[0]:
                best = (rank, key, display, source)
        if best:
            rank, key, display, source = best
            found.append({"heard": shown(heard), "suggested": display, "source": source,
                          "heard_count": len(spots), "suggested_count": len(seen.get(key, ())),
                          "lines": [{"idx": i, "line_id": lid} for i, lid in spots[:MAX_LINES_SHOWN]],
                          "_rank": rank})
        if truncated:
            break
    found.sort(key=lambda s: (s["_rank"], s["heard"]))
    for item in found:
        del item["_rank"]
    return {"suggestions": found[:MAX_SUGGESTIONS], "truncated": truncated or len(found) > MAX_SUGGESTIONS}
