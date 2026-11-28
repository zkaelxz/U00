"""Planted-mistake drill: `python -m tools.planted_mistake_drill SOURCE.srt GOLD.srt --language zh [--glossary G.json]`.

Measures what the app's own line checks catch. It takes a gold translation (a source file and its
right English, line for line), plants known mistakes in a copy by script with a fixed seed (English
moved onto the line before, a number changed, a glossary name swapped out, he and she swapped), runs
the source-match check (bge-m3 on the configured Ollama), Auto QC and the glossary check on it, and
reports for each check how many planted mistakes of each kind it caught and how many untouched lines
it flagged. The report holds counts only, never line text, so it can be shared. Nothing is saved to
the library and no translation engine is called; the source-match judge is not run.

The glossary file is a JSON list of {"original", "translation"}.
"""
import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/planted_mistake_drill.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Must run before the app imports below (see portable.py's docstring).
import portable
portable.activate_portable_mode()

import auto_qc
import subtitle_parse
from core import Line
from engine_backends import alignment_check, glossary_check

KINDS = ("shift", "number", "glossary", "pronoun")
CHECKS = ("source_match", "auto_qc", "glossary")
DEFAULT_RATE = 0.05
DEFAULT_SEED = 7
_DIGITS = re.compile(r"\d+")
_PRONOUN_SWAP = {"he": "she", "she": "he", "him": "her", "her": "him", "his": "her"}
_PRONOUN = re.compile(r"\b(he|she|him|her|his)\b", re.IGNORECASE)


def read_lines(path: str, language: str) -> list:
    with open(path, "rb") as fh:
        data = fh.read(subtitle_parse.MAX_FILE_BYTES + 1)
    parsed = subtitle_parse.parse_subtitle(data, os.path.basename(path), language=language)
    return [c.text.strip() for c in parsed.cues if c.text.strip()]


def load_glossary(path: str | None) -> list:
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as fh:
        terms = json.load(fh)
    return [{"term_original": t["original"], "term_translation": t["translation"]} for t in terms]


def _swap_pronouns(en: str) -> str:
    def swap(m):
        word = _PRONOUN_SWAP[m.group().lower()]
        return word.capitalize() if m.group()[0].isupper() else word
    return _PRONOUN.sub(swap, en)


def _bump_first_number(en: str) -> str:
    m = _DIGITS.search(en)
    return en[:m.start()] + str(int(m.group()) + 1) + en[m.end():] if m else en


def _drop_a_term(zh: str, en: str, terms) -> str:
    for _src, tr in glossary_check.required_terms(zh, terms):
        hit = re.search(re.escape(tr), en, re.IGNORECASE)
        if hit:
            return en[:hit.start()] + "someone" + en[hit.end():]
    return en


def _eligible(kind: str, zh: list, en: list, i: int, terms) -> bool:
    if kind == "shift":
        return i + 1 < len(en) and en[i + 1] != en[i]
    if kind == "number":
        return bool(_DIGITS.search(en[i])) and bool(auto_qc.source_numbers(zh[i])[0])
    if kind == "glossary":
        return _drop_a_term(zh[i], en[i], terms) != en[i]
    return bool(_PRONOUN.search(en[i]))


def plant(zh: list, gold: list, terms, rate: float = DEFAULT_RATE, seed: int = DEFAULT_SEED):
    """(planted English, {line index: kind}). Each kind takes `rate` of the lines (at least one where
    any line can carry it), never a line another kind already took, picked with the seed."""
    rng = random.Random(seed)
    en = list(gold)
    planted: dict = {}
    for kind in KINDS:
        pool = [i for i in range(len(gold)) if i not in planted and _eligible(kind, zh, gold, i, terms)]
        rng.shuffle(pool)
        for i in pool[:max(1, round(rate * len(gold)))]:
            planted[i] = kind
            en[i] = (gold[i + 1] if kind == "shift" else _bump_first_number(gold[i]) if kind == "number"
                     else _drop_a_term(zh[i], gold[i], terms) if kind == "glossary" else _swap_pronouns(gold[i]))
    return en, planted


def run_checks(zh: list, en: list, terms, embed_texts) -> dict:
    """{check: set of flagged line indexes}; source_match is None when the embedder is unusable."""
    lines = [Line(idx=i, start=float(i), end=i + 0.5, zh=z, en=e, id=i + 1) for i, (z, e) in enumerate(zip(zh, en))]
    source_match = None
    if embed_texts is not None:
        unchecked: list = []
        alignment_check.flag_mismatches(lines, embed_texts, unchecked=unchecked)
        if not (unchecked and len(unchecked) == len(lines)):
            source_match = {ln.idx for ln in lines if ln.flag == alignment_check.ALIGNMENT_FLAG}
    names = auto_qc.build_name_list(terms)
    return {
        "source_match": source_match,
        "auto_qc": {i for i, (z, e) in enumerate(zip(zh, en)) if auto_qc.check_line(z, e, names)},
        "glossary": {i for i, (z, e) in enumerate(zip(zh, en)) if glossary_check.missing_terms(z, e, terms)},
    }


def score(planted: dict, flagged: dict, total: int) -> dict:
    """Counts only: per check, caught of planted per kind, and false flags on the untouched lines."""
    controls = [i for i in range(total) if i not in planted]
    out = {}
    for check in CHECKS:
        hits = flagged.get(check)
        if hits is None:
            out[check] = None
            continue
        out[check] = {
            "caught": {k: sum(1 for i, kind in planted.items() if kind == k and i in hits) for k in KINDS},
            "false_flags": sum(1 for i in controls if i in hits),
        }
    any_hits = set().union(*(h for h in flagged.values() if h is not None))
    out["any"] = {"caught": {k: sum(1 for i, kind in planted.items() if kind == k and i in any_hits) for k in KINDS},
                  "false_flags": sum(1 for i in controls if i in any_hits)}
    return {"lines": total, "controls": len(controls),
            "planted": {k: sum(1 for kind in planted.values() if kind == k) for k in KINDS}, "checks": out}


def format_report(result: dict, seed: int) -> str:
    planted = result["planted"]
    rows = ["# Planted-mistake drill", "",
            f"{result['lines']} lines, seed {seed}; planted: "
            + ", ".join(f"{planted[k]} {k}" for k in KINDS) + f"; {result['controls']} untouched lines.", "",
            "| Check | " + " | ".join(KINDS) + " | False flags |", "|---" * (len(KINDS) + 2) + "|"]
    for check in (*CHECKS, "any"):
        r = result["checks"][check]
        if r is None:
            rows.append(f"| {check} | " + " | ".join("-" for _ in KINDS) + " | not run (embedder unusable) |")
            continue
        cells = [f"{r['caught'][k]}/{planted[k]}" for k in KINDS]
        share = r["false_flags"] / result["controls"] if result["controls"] else 0.0
        rows.append(f"| {check} | " + " | ".join(cells) + f" | {r['false_flags']} ({share:.1%}) |")
    return "\n".join(rows) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.planted_mistake_drill", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("source", help="source subtitle file")
    parser.add_argument("gold", help="the right English, line for line with the source")
    parser.add_argument("--language", required=True, help="source language (zh, ja, ko)")
    parser.add_argument("--glossary", help='JSON list of {"original", "translation"}')
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help=f"share of lines per mistake kind (default {DEFAULT_RATE})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"planting seed (default {DEFAULT_SEED})")
    parser.add_argument("--no-embed", action="store_true", help="skip the source-match check (no Ollama)")
    parser.add_argument("--out", help="also write the counts as JSON here")
    opts = parser.parse_args(argv)
    if not 0 < opts.rate <= 0.25:
        parser.error("--rate must be above 0 and at most 0.25")
    zh, gold = read_lines(opts.source, opts.language), read_lines(opts.gold, opts.language)
    if len(zh) != len(gold):
        print(f"Refused: the source has {len(zh)} lines but the gold English {len(gold)}; they must match line for line.")
        return 2
    terms = load_glossary(opts.glossary)
    en, planted = plant(zh, gold, terms, opts.rate, opts.seed)
    embed_texts = None
    if not opts.no_embed:
        from services import settings_service
        base_url = settings_service.ollama_url()
        embed_texts = lambda texts: alignment_check.embed(base_url, texts)  # noqa: E731
    result = score(planted, run_checks(zh, en, terms, embed_texts), len(zh))
    print(format_report(result, opts.seed))
    if opts.out:
        Path(opts.out).write_text(json.dumps({"seed": opts.seed, **result}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
