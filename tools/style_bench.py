"""Genre style bench: `python -m tools.style_bench {check,score,blind,unblind} ROOT ...`.

Compares the app's comic and novel translations with a human translation the
owner likes, per genre and series, so a per-genre style note can be judged by
how close it gets. Nothing here calls an engine or opens the library: it reads
a sample folder laid out as

    ROOT/{comic,novel}/<genre>/<series>/
        original/   page pictures (comic) or chapter text files (novel)
        reference/  the human English, one .txt per page or chapter, same stem
        runs/<run>/ the app's English for some or all of those items, same stem

`score` writes ROOT/style-report.md: chrF and chrF++ against the reference
plus a style fingerprint (sentence length, contractions, honorifics kept, ...)
beside the reference's own. `blind` writes a self-contained tap page and a
sealed key for one series; `unblind` tallies the copied results against it.
"""
import argparse
import base64
import html
import json
import random
import secrets
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

MEDIA = ("comic", "novel")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
REPORT_NAME = "style-report.md"
KEY_NAME = "KEY-do-not-open-until-scored.json"
PAGE_NAME = "blind.html"
# A blind card holds this many lines of a novel chapter, so a long chapter is
# judged a passage at a time instead of as one wall of text.
NOVEL_LINES_PER_CARD = 6
MAX_CARDS = 120
MAX_VERSIONS = 4

# Suffixes count only after a hyphen ("Mina-san"), since "san" or "chan" alone
# is often an English word or a name; the rest also stand alone ("Gege!").
HONORIFIC_SUFFIXES = ("san", "kun", "chan", "sama", "dono", "nim", "ssi")
HONORIFIC_WORDS = (
    "senpai", "sensei", "oppa", "unnie", "eonni", "hyung", "noona", "nuna", "sunbae", "gege", "jiejie",
    "didi", "meimei", "shixiong", "shijie", "shizun", "shifu", "laoshi", "xiansheng", "xiaojie", "daren",
)
_HONORIFIC = re.compile(r"(?:-(?:%s)\b|\b(?:%s)\b)" % ("|".join(HONORIFIC_SUFFIXES + HONORIFIC_WORDS),
                                                       "|".join(HONORIFIC_WORDS)), re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
_CONTRACTION = re.compile(r"\b[A-Za-z]+['’](?:s|re|ve|ll|d|t|m)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s|$)")


@dataclass
class Series:
    medium: str
    genre: str
    name: str
    path: Path
    originals: dict = field(default_factory=dict)   # stem -> Path
    references: dict = field(default_factory=dict)  # stem -> Path (.txt)
    reference_images: list = field(default_factory=list)
    runs: dict = field(default_factory=dict)        # run name -> {stem -> Path}

    @property
    def label(self) -> str:
        return f"{self.medium}/{self.genre}/{self.name}"


def _files(folder: Path, exts) -> dict:
    if not folder.is_dir():
        return {}
    return {p.stem: p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in exts}


def find_series(root: Path) -> list:
    out = []
    for medium in MEDIA:
        for genre_dir in sorted(p for p in (root / medium).glob("*") if p.is_dir()):
            for series_dir in sorted(p for p in genre_dir.iterdir() if p.is_dir()):
                out.append(load_series(series_dir, medium, genre_dir.name))
    return out


def load_series(path: Path, medium: str | None = None, genre: str | None = None) -> Series:
    # Resolved so a relative path such as "." still yields its medium and genre folders.
    path = path.resolve()
    medium = medium or path.parent.parent.name
    s = Series(medium, genre or path.parent.name, path.name, path)
    s.originals = _files(path / "original", IMAGE_EXTS if medium == "comic" else {".txt"})
    s.references = _files(path / "reference", {".txt"})
    s.reference_images = sorted(_files(path / "reference", IMAGE_EXTS))
    for run_dir in sorted(p for p in (path / "runs").glob("*") if p.is_dir()):
        s.runs[run_dir.name] = _files(run_dir, {".txt"})
    return s


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").strip()


def text_lines(text: str) -> list:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


# ---------------------------------------------------------------------------
# check

def check(root: Path) -> list:
    """One plain line per problem or per series, for the owner to fix the folder."""
    notes = []
    all_series = find_series(root)
    if not all_series:
        return [f"No series found. Make folders like {root / 'comic' / 'romance' / 'my-series' / 'original'}."]
    for s in all_series:
        missing_ref = [k for k in s.originals if k not in s.references and k not in s.reference_images]
        orphan_ref = [k for k in s.references if k not in s.originals]
        notes.append(f"{s.label}: {len(s.originals)} originals, {len(s.references)} reference texts, "
                     f"{len(s.runs)} runs")
        if missing_ref:
            notes.append(f"  no reference for: {', '.join(missing_ref[:10])}")
        if orphan_ref:
            notes.append(f"  reference with no original: {', '.join(orphan_ref[:10])}")
        if s.reference_images:
            notes.append(f"  {len(s.reference_images)} reference pictures still need their English typed "
                         "or read out to .txt")
    return notes


# ---------------------------------------------------------------------------
# score

def chrf(hyp: str, ref: str, word_order: int = 0) -> float:
    import sacrebleu
    return sacrebleu.sentence_chrf(hyp, [ref], word_order=word_order).score / 100.0


def fingerprint(text: str) -> dict:
    words = _WORD.findall(text)
    n_words = max(len(words), 1)
    sentences = max(len(_SENTENCE_END.findall(text)), 1)
    return {
        "words": len(words),
        "words_per_sentence": round(len(words) / sentences, 1),
        "contractions_per_100w": round(100 * len(_CONTRACTION.findall(text)) / n_words, 1),
        "honorifics_per_100w": round(100 * len(_HONORIFIC.findall(text)) / n_words, 2),
        "exclaims_per_100s": round(100 * text.count("!") / sentences, 1),
        "ellipses_per_100s": round(100 * (text.count("...") + text.count("…")) / sentences, 1),
        "dashes_per_100s": round(100 * (text.count("—") + text.count("--")) / sentences, 1),
    }


FINGERPRINT_LABELS = {
    "words_per_sentence": "Words per sentence",
    "contractions_per_100w": "Contractions per 100 words",
    "honorifics_per_100w": "Honorifics kept per 100 words",
    "exclaims_per_100s": "! per 100 sentences",
    "ellipses_per_100s": "... per 100 sentences",
    "dashes_per_100s": "Dashes per 100 sentences",
}


def score_run(s: Series, run: str) -> dict:
    """Scores only the items the run and the reference both have, so a run made
    on a held-out half is compared with that half of the reference."""
    stems = [k for k in s.runs[run] if k in s.references]
    hyp = "\n".join(read_text(s.runs[run][k]) for k in stems)
    ref = "\n".join(read_text(s.references[k]) for k in stems)
    if not stems:
        return {"items": 0}
    per_item = {k: round(chrf(read_text(s.runs[run][k]), read_text(s.references[k])), 3) for k in stems}
    return {
        "items": len(stems), "stems": stems,
        "chrf": round(chrf(hyp, ref), 3), "chrf_pp": round(chrf(hyp, ref, word_order=2), 3),
        "worst": sorted(per_item.items(), key=lambda kv: kv[1])[:3],
        "length_ratio": round(len(_WORD.findall(hyp)) / max(len(_WORD.findall(ref)), 1), 2),
        "fingerprint": fingerprint(hyp),
    }


def build_report(root: Path) -> str:
    out = ["# Genre style bench report", "",
           "chrF and chrF++ run 0 to 1: higher is closer to the human translation's wording. "
           "They reward the same words, not the same quality, so read them beside the blind marks. "
           "Length ratio is the run's word count over the reference's (1.00 = same length).", ""]
    by_genre: dict = {}
    for s in find_series(root):
        if not s.runs:
            continue
        out += [f"## {s.label}", "",
                "| Run | Items | chrF | chrF++ | Length ratio | Lowest items |", "|---|---|---|---|---|---|"]
        scored = {}
        for run in s.runs:
            r = score_run(s, run)
            if not r["items"]:
                out.append(f"| {run} | 0 | | | | no file matches a reference |")
                continue
            scored[run] = r
            by_genre.setdefault((s.medium, s.genre, run), []).append(r["chrf"])
            worst = ", ".join(f"{k} {v:.2f}" for k, v in r["worst"])
            out.append(f"| {run} | {r['items']} | {r['chrf']:.3f} | {r['chrf_pp']:.3f} | "
                       f"{r['length_ratio']:.2f} | {worst} |")
        if scored:
            out += ["", "Style fingerprint (the reference column is the target):", "",
                    "| Measure | Reference | " + " | ".join(scored) + " |",
                    "|---|---|" + "---|" * len(scored)]
            # The translator's whole reference is the target, whichever items each run covers.
            ref_fp = fingerprint("\n".join(read_text(p) for p in s.references.values()))
            for key, label in FINGERPRINT_LABELS.items():
                out.append(f"| {label} | {ref_fp[key]} | " + " | ".join(str(r["fingerprint"][key])
                                                                       for r in scored.values()) + " |")
        out.append("")
    if by_genre:
        out += ["## Average chrF per genre", "", "| Medium | Genre | Run | Series | Mean chrF |",
                "|---|---|---|---|---|"]
        for (medium, genre, run), vals in sorted(by_genre.items()):
            out.append(f"| {medium} | {genre} | {run} | {len(vals)} | {statistics.mean(vals):.3f} |")
    else:
        out.append("No runs to score yet.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# blind

def _cards(s: Series, runs: list, with_reference: bool) -> list:
    """Each card is one comic page, or a passage of a novel chapter when every
    version has the same line count (else the whole chapter)."""
    versions = list(runs) + (["reference"] if with_reference else [])
    cards = []
    for stem in s.originals:
        texts = {}
        for v in versions:
            src = s.references if v == "reference" else s.runs.get(v, {})
            if stem in src:
                texts[v] = read_text(src[stem])
        if len(texts) < len(versions):
            continue
        source = read_text(s.originals[stem]) if s.medium == "novel" else ""
        split = text_lines(source) if source else []
        by_v = {v: text_lines(t) for v, t in texts.items()}
        if s.medium == "novel" and split and all(len(ls) == len(split) for ls in by_v.values()):
            for i in range(0, len(split), NOVEL_LINES_PER_CARD):
                cards.append({"item": stem, "part": i // NOVEL_LINES_PER_CARD + 1,
                              "source": "\n".join(split[i:i + NOVEL_LINES_PER_CARD]),
                              "texts": {v: "\n".join(ls[i:i + NOVEL_LINES_PER_CARD]) for v, ls in by_v.items()}})
        else:
            cards.append({"item": stem, "part": 1, "source": source, "texts": texts})
    return cards[:MAX_CARDS]


def _image_uri(p: Path) -> str:
    mime = "image/png" if p.suffix.lower() == ".png" else (
        "image/webp" if p.suffix.lower() == ".webp" else "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def make_blind(series_dir: Path, runs: list, with_reference: bool, seed: int | None = None) -> tuple:
    s = load_series(series_dir)
    unknown = [r for r in runs if r not in s.runs]
    if unknown:
        raise SystemExit(f"No such run folder: {', '.join(unknown)} (have: {', '.join(s.runs) or 'none'})")
    if not 2 <= len(runs) + int(with_reference) <= MAX_VERSIONS:
        raise SystemExit(f"A blind test needs 2 to {MAX_VERSIONS} versions.")
    rng = random.Random(seed)
    cards, key = [], []
    for n, card in enumerate(_cards(s, runs, with_reference), 1):
        order = list(card["texts"])
        rng.shuffle(order)
        letters = "ABCD"[:len(order)]
        key.append({"card": n, "item": card["item"], "part": card["part"], **dict(zip(letters, order))})
        cards.append({"n": n, "title": f"{card['item']}" + (f" part {card['part']}" if card["part"] > 1 else ""),
                      "source": card["source"],
                      "image": _image_uri(s.originals[card["item"]]) if s.medium == "comic" else "",
                      "versions": [[ltr, card["texts"][v]] for ltr, v in zip(letters, order)]})
    if not cards:
        raise SystemExit("No item has every version yet; run `check` to see what is missing.")
    # A new build id per page keeps picks saved for an earlier shuffle (or another
    # series: file:// pages share one browser storage) from being read against this key.
    build = secrets.token_hex(6)
    return _page(s.label, cards, build), {"series": s.label, "build": build, "runs": runs,
                                          "with_reference": with_reference, "cards": key}


def _page(label: str, cards: list, build: str) -> str:
    data = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.replace("__TITLE__", html.escape(label)).replace("__BUILD__", build).replace("__DATA__", data)


def unblind(key: dict, results: dict) -> str:
    """results maps card number -> a letter, "tie" or "skip" (the page's Copy results)."""
    wins: dict = {}
    ties = skipped = 0
    for row in key["cards"]:
        pick = str(results.get(str(row["card"]), "skip"))
        if pick == "tie":
            ties += 1
        elif pick in row:
            wins[row[pick]] = wins.get(row[pick], 0) + 1
        else:
            skipped += 1
    total = len(key["cards"])
    return (f"Blind result for {key['series']} ({total} cards): "
            + ", ".join(f"{v} {n}" for v, n in sorted(wins.items(), key=lambda kv: -kv[1]))
            + f", tie {ties}, not marked {skipped}.")


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind style test</title>
<style>
:root{--bg:#f6f6f3;--surface:#fff;--fg:#1d2420;--muted:#5d6862;--line:#dcdfd9;--accent:#2f5d50;--accent-fg:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#131815;--surface:#1b221e;--fg:#e6ebe7;
--muted:#9aa69f;--line:#2d3731;--accent:#7fc4ae;--accent-fg:#0f1512;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif;padding:0 16px 48px}
.wrap{max-width:880px;margin:0 auto}header{position:sticky;top:0;background:var(--bg);padding:12px 0;border-bottom:1px solid var(--line);z-index:2}
h1{font-size:19px;margin:0}.sub{color:var(--muted);font-size:13px;margin:2px 0 8px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px;margin:14px 0}
.card h2{font-size:14px;margin:0 0 8px;color:var(--muted)}.src{white-space:pre-wrap;color:var(--muted);margin-bottom:10px}
.card img{max-width:100%;height:auto;border-radius:4px;margin-bottom:10px}
.v{white-space:pre-wrap;border:1px solid var(--line);border-radius:6px;padding:10px;margin:8px 0}
.v b{display:block;font-size:12px;color:var(--muted)}
.picks{display:flex;gap:6px;flex-wrap:wrap}.picks button,.btn{font:inherit;border:1px solid var(--line);background:var(--surface);
color:var(--fg);padding:8px 14px;border-radius:6px;cursor:pointer;min-height:40px}
.picks button[aria-pressed="true"]{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}
textarea{width:100%;min-height:90px;font:12px monospace;background:var(--surface);color:var(--fg);border:1px solid var(--line)}
</style></head><body><div class="wrap">
<header><h1>Blind style test: __TITLE__</h1><p class="sub">Pick the version that reads best to you on each card. Versions are shuffled on every card.
<span id="count"></span></p><button class="btn" id="copy">Copy results</button></header>
<div id="cards"></div><textarea id="out" readonly aria-label="Results to paste back"></textarea></div>
<script>
const CARDS=__DATA__;const KEY="style-blind:__BUILD__";
let picks={};try{picks=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
function save(){try{localStorage.setItem(KEY,JSON.stringify(picks))}catch(e){}
document.getElementById("count").textContent=Object.keys(picks).length+" of "+CARDS.length+" marked.";
document.getElementById("out").value=JSON.stringify(picks)}
const root=document.getElementById("cards");
for(const c of CARDS){const el=document.createElement("section");el.className="card";
const h=document.createElement("h2");h.textContent="Card "+c.n+" · "+c.title;el.appendChild(h);
if(c.image){const i=document.createElement("img");i.src=c.image;i.alt="Original page";i.loading="lazy";el.appendChild(i)}
if(c.source){const s=document.createElement("div");s.className="src";s.textContent=c.source;el.appendChild(s)}
for(const [l,t] of c.versions){const v=document.createElement("div");v.className="v";const b=document.createElement("b");
b.textContent="Version "+l;v.appendChild(b);v.appendChild(document.createTextNode(t));el.appendChild(v)}
const p=document.createElement("div");p.className="picks";
for(const choice of [...c.versions.map(v=>v[0]),"tie"]){const btn=document.createElement("button");
btn.textContent=choice==="tie"?"Same":choice;btn.setAttribute("aria-pressed",picks[c.n]===choice?"true":"false");
btn.onclick=()=>{picks[c.n]=choice;for(const x of p.children)x.setAttribute("aria-pressed",x===btn?"true":"false");save()};
p.appendChild(btn)}el.appendChild(p);root.appendChild(el)}
document.getElementById("copy").onclick=()=>{save();const o=document.getElementById("out");o.select();
try{navigator.clipboard.writeText(o.value)}catch(e){}};save();
</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("root", type=Path)
    sub.add_parser("score").add_argument("root", type=Path)
    b = sub.add_parser("blind")
    b.add_argument("series", type=Path)
    b.add_argument("--runs", required=True, help="comma-separated run folder names")
    b.add_argument("--with-reference", action="store_true",
                   help="hide the human translation among the versions too")
    b.add_argument("--seed", type=int)
    u = sub.add_parser("unblind")
    u.add_argument("series", type=Path)
    u.add_argument("results", help="the text from Copy results, or a file holding it")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        print("\n".join(check(args.root)))
    elif args.cmd == "score":
        report = build_report(args.root)
        (args.root / REPORT_NAME).write_text(report, encoding="utf-8")
        print(report)
    elif args.cmd == "blind":
        page, key = make_blind(args.series, [r.strip() for r in args.runs.split(",") if r.strip()],
                               args.with_reference, args.seed)
        (args.series / PAGE_NAME).write_text(page, encoding="utf-8")
        (args.series / KEY_NAME).write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Wrote {args.series / PAGE_NAME} ({len(key['cards'])} cards) and the sealed key.")
    else:
        raw = args.results
        if not raw.lstrip().startswith("{"):
            raw = Path(raw).read_text(encoding="utf-8")
        key = json.loads((args.series / KEY_NAME).read_text(encoding="utf-8"))
        print(unblind(key, json.loads(raw)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
