"""glossary_candidates -- recurring names and terms found in source lines with no model call."""
import math
import re
from collections import Counter, defaultdict
from typing import Iterable, List, Tuple

import glossary_phonetic as ph

MIN_COUNT = 2           # a string seen once is not recurring
MIN_COHESION = 30.0     # how much likelier the pieces are together than by chance
MIN_EDGE_ENTROPY = 0.3  # a real term has varied neighbours; a fragment of a longer word does not
MAX_GRAM = 4
MAX_RESULTS = 60

# CJK characters are one token each (no word spaces); digits with a percent sign and Latin
# words stay whole because a nickname like "20%仔" is built from them.
_TOKEN = re.compile(r"[぀-ヿ一-鿿가-힣]|[0-9]+%?|[A-Za-z]+")


def _entropy(counts: Counter):
    total = sum(counts.values())
    if not total:
        return None
    return -sum(v / total * math.log(v / total) for v in counts.values())


def find_candidates(lines: Iterable[str], known_terms: Iterable[str] = (),
                    min_count: int = MIN_COUNT) -> List[Tuple[str, int]]:
    """[(text, count)] of recurring strings that look like one unit, most frequent first.

    Strings that contain, or sit inside, a term already in the glossary are left out.
    Counts are per occurrence; a string must also span at least `min_count` lines."""
    known = [k for k in known_terms if k]
    token_lines = [_TOKEN.findall(s or "") for s in lines]
    total = sum(len(t) for t in token_lines)
    if not total:
        return []
    count: Counter = Counter()
    left, right = defaultdict(Counter), defaultdict(Counter)
    spans = defaultdict(set)
    single = Counter(tok for toks in token_lines for tok in toks)
    for li, toks in enumerate(token_lines):
        for n in range(2, MAX_GRAM + 1):
            for i in range(len(toks) - n + 1):
                gram = "".join(toks[i:i + n])
                count[gram] += 1
                spans[gram].add(li)
                if i:
                    left[gram][toks[i - 1]] += 1
                if i + n < len(toks):
                    right[gram][toks[i + n]] += 1

    def freq(text: str) -> int:
        return count.get(text) or single.get(text, 0) or 1

    def cohesion(gram: str) -> float:
        toks = _TOKEN.findall(gram)
        # The weakest split decides: "喜歡" glued from two common characters scores low,
        # a name whose characters rarely appear apart scores high.
        return min((count[gram] / total) / (freq("".join(toks[:k])) / total * freq("".join(toks[k:])) / total)
                   for k in range(1, len(toks)))

    kept = []
    for gram, c in count.items():
        if c < min_count or len(spans[gram]) < min_count:
            continue
        if any(k in gram or gram in k for k in known):
            continue
        # A line edge says nothing about the neighbour, and one neighbour seen once has no
        # spread to measure; counting either as "no variety" would throw out a nickname
        # that always opens its line.
        sides = [_entropy(side[gram]) for side in (left, right) if sum(side[gram].values()) >= 2]
        if sides and min(sides) < MIN_EDGE_ENTROPY:
            continue
        if cohesion(gram) < MIN_COHESION:
            continue
        kept.append((gram, c))
    # "天珠" inside a more frequent "天珠峰" is reported once, as the shorter unit.
    kept = [(g, c) for g, c in kept if not any(g != h and g in h and c <= d for h, d in kept)]
    return sorted(kept, key=lambda x: (-x[1], x[0]))[:MAX_RESULTS]


def group_by_sound(candidates: List[Tuple[str, int]], language: str) -> List[List[Tuple[str, int]]]:
    """Groups of candidates that sound alike ("these may be one name"). Empty when the
    reading library for `language` is not installed: spellings are then reported singly."""
    if not ph.availability(language)["available"]:
        return []
    groups: List[dict] = []
    for text, c in candidates:
        key = ph.phonetic_key(text, language)
        if len(key) < 2:
            continue
        for g in groups:
            if ph.similar(key, g["key"], language):
                g["members"].append((text, c))
                break
        else:
            groups.append({"key": key, "members": [(text, c)]})
    return [g["members"] for g in groups if len(g["members"]) > 1]
