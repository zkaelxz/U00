"""Scanlate quality bench: `python -m tools.scanlate_bench [--pages N] [--langs ja,zh,ko]
[--ocr BACKEND] [--detect cv|ml|auto] [--set NAME=VALUE ...] [--out DIR]`.

Draws a fixed, seeded set of synthetic comic pages whose answers are known
(panels, screentone and line art, speech balloons with vertical and horizontal
CJK lettering, some pages degraded like a scan) and runs them through the app's
own Scanlate code: bubble detection, reading order, OCR, text cleanup and
typesetting. Each stage is scored against the known answer and the results go
to one markdown report and one JSON file. Nothing in the library is read or
changed. `--set` overrides a value in scanlate_tuning for this run only, so two
runs compare a change before it is made.

Synthetic pages measure the pipeline's mechanics, not how it does on a given
real series; docs/technical-notes.md ("Scanlate quality bench") says what
each number means.
"""
import argparse
import dataclasses
import json
import os
import random
import statistics
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/scanlate_bench.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAGE_W, PAGE_H = 1100, 1600
REPORT_NAME = "scanlate-bench-report.md"
JSON_NAME = "scanlate-bench.json"
LANGS = ("ja", "zh", "ko")
# How each source language's comics are read: Japanese manga right to left,
# Chinese manhua and Korean manhwa left to right. The pages are drawn to this.
READS_RIGHT_TO_LEFT = {"ja": True, "zh": False, "ko": False}
MATCH_IOU = 0.5
# A balloon counts as found when a predicted box covers this much of its lettering
# and sits inside the balloon: a text-only box is a usable region too.
MATCH_TEXT_COVER = 0.8
OVERFLOW_PIXELS = 12
# Tallest-letter height (the 90th percentile of the English ink's glyph heights)
# below which a phone screen makes a balloon hard to read.
TINY_LETTER_PX = 13

# Phrases avoid punctuation that vertical lettering rotates or moves; the
# scorer ignores punctuation anyway.
PHRASES = {
    "ja": ["今日は雨が降っている", "どこへ行くつもりなの", "早く逃げて", "私は知らない", "それは本当か",
           "先生が呼んでいるよ", "明日また会おう", "絶対に負けない", "何を言っているんだ", "ここは危ない",
           "お腹がすいた", "約束を忘れないで", "大丈夫だから", "夢を見ていた", "あの人は誰",
           "もう一度だけ", "静かにしなさい", "信じているよ", "私の名前は花子", "電車に乗り遅れた",
           "本を読んでいる", "海が見たい", "ありがとう", "待っていたよ", "それは秘密です"],
    "zh": ["今天下雨了", "你要去哪里", "快点跑", "我不知道", "这是真的吗", "老师在叫你",
           "明天再见", "我绝对不会输", "你在说什么", "这里很危险", "我肚子饿了", "别忘了约定",
           "没关系的", "我做了一个梦", "那个人是谁", "再来一次", "请安静", "我相信你",
           "我的名字叫小花", "我错过了火车", "他在看书", "我想去看海", "谢谢你", "我一直在等你",
           "这是秘密"],
    "ko": ["오늘은 비가 온다", "어디 가려고", "빨리 도망쳐", "나는 몰라", "그게 정말이야",
           "선생님이 부르셔", "내일 또 만나", "절대 지지 않아", "무슨 소리야", "여기는 위험해",
           "배고파", "약속 잊지 마", "괜찮아", "꿈을 꾸었어", "저 사람은 누구야", "한 번만 더",
           "조용히 해", "너를 믿어", "내 이름은 하나야", "기차를 놓쳤어", "책을 읽고 있어",
           "바다가 보고 싶어", "고마워", "기다리고 있었어", "그건 비밀이야"],
}

# English lines of a typical scanlation length, from short to long, plus the
# shapes that break naive wrapping (one long word, a name with a hyphen).
ENGLISH = [
    "No way!", "Run!", "Who is that?", "I don't know.", "Thank you.",
    "See you tomorrow.", "It's raining today.", "Where are you going?",
    "The teacher is calling you.", "I will never lose to you.", "What are you even saying?",
    "Don't forget our promise, okay?", "I missed the last train home again.",
    "I've been waiting for you this whole time.", "This place is dangerous, we have to leave now!",
    "Unbelievable!", "Hanako-san, wait for me!", "Absolutely-not-happening.",
    "If you really believe that, then why are you still standing here?",
]

# English runs about this many letters per source character in a scanlation.
ENGLISH_PER_SOURCE_CHAR = {"ja": 3.0, "zh": 3.5, "ko": 2.0}


def english_for(source: str, lang: str, rng) -> str:
    """An English line about as long as a translation of `source` would be."""
    target = len(source) * ENGLISH_PER_SOURCE_CHAR[lang] * rng.uniform(0.75, 1.3)
    return min(ENGLISH, key=lambda e: (abs(len(e) - target), e))


# Fonts with CJK coverage, per language, tried in order. Windows names first:
# the bench is mostly run on the owner's PC.
_WINDOWS_FONTS = os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts")
FONT_CANDIDATES = {
    "ja": [os.path.join(_WINDOWS_FONTS, "msgothic.ttc"), os.path.join(_WINDOWS_FONTS, "YuGothM.ttc"),
           "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
           "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
           "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
           "/System/Library/Fonts/Hiragino Sans GB.ttc"],
    "zh": [os.path.join(_WINDOWS_FONTS, "msyh.ttc"), os.path.join(_WINDOWS_FONTS, "simhei.ttf"),
           "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
           "/System/Library/Fonts/Hiragino Sans GB.ttc"],
    "ko": [os.path.join(_WINDOWS_FONTS, "malgun.ttf"),
           "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
           "/System/Library/Fonts/AppleSDGothicNeo.ttc"],
}


class BenchError(RuntimeError):
    """A problem the person running the bench can fix (no font, no OCR)."""


def find_font(lang: str, override: str = None) -> str:
    if override:
        if not os.path.exists(override):
            raise BenchError(f"Font not found: {override}")
        return override
    for path in FONT_CANDIDATES.get(lang, []):
        if os.path.exists(path):
            return path
    raise BenchError(f"No font with {lang} characters was found; pass --font FILE.")


# ---------------------------------------------------------------------------
# Synthetic pages
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Balloon:
    x: int
    y: int
    w: int
    h: int
    text: str
    vertical: bool
    glyph: int
    text_box: tuple          # (x, y, w, h) of the lettering
    shape: str               # "ellipse" or "round"
    order: int = 0


@dataclasses.dataclass
class Page:
    name: str
    lang: str
    image: Image.Image       # RGB
    balloons: list
    clean: Image.Image       # the same page with every balloon left empty
    degraded: bool


def _lettering_size(text: str, glyph: int, vertical: bool, per_col: int) -> tuple:
    step = int(glyph * 1.15)
    if vertical:
        cols = [text[i:i + per_col] for i in range(0, len(text), per_col)]
        return cols, len(cols) * step, max(len(c) for c in cols) * glyph
    words = text.split(" ") if " " in text else None
    if words:      # Korean breaks between words
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cur and len(trial) > per_col:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
    else:
        lines = [text[i:i + per_col] for i in range(0, len(text), per_col)]
    return lines, max(len(t) for t in lines) * glyph, len(lines) * step


def _draw_lettering(draw, font, lines, x, y, glyph, vertical):
    step = int(glyph * 1.15)
    if vertical:
        # Columns run right to left.
        for ci, col in enumerate(lines):
            cx = x + (len(lines) - 1 - ci) * step
            for ri, ch in enumerate(col):
                draw.text((cx, y + ri * glyph), ch, font=font, fill=0)
    else:
        for li, line in enumerate(lines):
            draw.text((x, y + li * step), line, font=font, fill=0)


def _draw_art(draw, rng, x0, y0, x1, y1):
    kind = rng.choice(["tone", "hatch", "white", "dark"])
    if kind == "tone":
        for yy in range(y0 + 4, y1 - 4, 7):
            for xx in range(x0 + 4 + (yy // 7 % 2) * 3, x1 - 4, 7):
                draw.ellipse((xx, yy, xx + 2, yy + 2), fill=150)
    elif kind == "hatch":
        for k in range(x0 - (y1 - y0), x1, 11):
            draw.line((k, y1, k + (y1 - y0), y0), fill=110, width=1)
        draw.rectangle((x0 - 20, y0 - 20, x0 - 1, y1 + 20), fill=255)   # hatch spill is cropped below
    elif kind == "dark":
        top = rng.randint(y0, (y0 + y1) // 2)
        draw.rectangle((x0, top, x1, y1), fill=rng.randint(40, 90))
    # Line art: a few outlines and strokes, as a figure or background would have.
    for _ in range(rng.randint(2, 5)):
        cx, cy = rng.randint(x0, x1), rng.randint(y0, y1)
        r = rng.randint(20, 90)
        draw.ellipse((cx - r, cy - r // 2, cx + r, cy + r), outline=0, width=3)
    for _ in range(rng.randint(2, 6)):
        draw.line((rng.randint(x0, x1), rng.randint(y0, y1), rng.randint(x0, x1), rng.randint(y0, y1)),
                  fill=0, width=rng.randint(2, 4))


def _draw_balloon(draw, rng, b: Balloon, panel):
    x0, y0, x1, y1 = b.x, b.y, b.x + b.w - 1, b.y + b.h - 1
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    # Tail toward the panel's middle, drawn under the balloon so only its tip shows.
    px, py = (panel[0] + panel[2]) // 2, (panel[1] + panel[3]) // 2
    dx, dy = px - cx, py - cy
    norm = max((dx * dx + dy * dy) ** 0.5, 1)
    tip = (int(cx + dx / norm * (b.w / 2 + 30)), int(cy + dy / norm * (b.h / 2 + 30)))
    base = [(int(cx - dy / norm * 14), int(cy + dx / norm * 14)),
            (int(cx + dy / norm * 14), int(cy - dx / norm * 14))]
    draw.polygon(base + [tip], fill=255, outline=0, width=3)
    if b.shape == "ellipse":
        draw.ellipse((x0, y0, x1, y1), fill=255, outline=0, width=3)
    else:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=min(b.w, b.h) // 3, fill=255, outline=0, width=3)
    # Open the outline where the tail joins.
    draw.polygon([base[0], base[1], (int(cx + dx / norm * (b.w / 2)), int(cy + dy / norm * (b.h / 2)))],
                 fill=255)


def _plan_balloon(rng, lang, panel, slot, font_size, vertical):
    text = rng.choice(PHRASES[lang])
    per = rng.randint(4, 6) if vertical else rng.randint(5, 8)
    lines, tw, th = _lettering_size(text, font_size, vertical, per)
    pad_x, pad_y = (0.32, 0.22) if vertical else (0.28, 0.40)
    w = int(tw * (1 + 2 * pad_x)) + 16
    h = int(th * (1 + 2 * pad_y)) + 16
    return text, lines, tw, th, w, h


def make_page(index: int, lang: str, fonts: dict, seed: int = 0) -> Page:
    """One synthetic page. Same (index, lang, seed) -> same pixels and answers."""
    rng = random.Random(f"{seed}-{lang}-{index}")
    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)
    rtl = READS_RIGHT_TO_LEFT[lang]
    margin, gutter = 50, rng.choice([18, 24, 30])
    rows = rng.choice([3, 3, 4])
    row_h = (PAGE_H - 2 * margin - (rows - 1) * gutter) // rows
    panels = []
    for r in range(rows):
        y0 = margin + r * (row_h + gutter)
        cols = rng.choice([1, 2, 2])
        split = rng.uniform(0.38, 0.62)
        xs = ([(margin, PAGE_W - margin)] if cols == 1 else
              [(margin, int(margin + (PAGE_W - 2 * margin) * split) - gutter // 2),
               (int(margin + (PAGE_W - 2 * margin) * split) + gutter // 2, PAGE_W - margin)])
        row = [(x0, y0, x1, y0 + row_h) for x0, x1 in xs]
        panels.extend(reversed(row) if rtl else row)
    for p in panels:
        _draw_art(draw, rng, *p)
    # Gutters stay white whatever the art spilled.
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    for p in panels:
        md.rectangle(p, fill=255)
    img = Image.composite(img, Image.new("L", img.size, 255), mask)
    draw = ImageDraw.Draw(img)
    for p in panels:
        draw.rectangle(p, outline=0, width=4)

    balloons, letterings = [], []
    font_cache = {}
    for p in panels:
        pw, ph = p[2] - p[0], p[3] - p[1]
        n = rng.choice([1, 1, 2, 2]) if pw > 300 else 1
        side_by_side = n == 2 and rng.random() < 0.4
        placed = []
        for k in range(n):
            vertical = lang == "ja" or (lang == "zh" and rng.random() < 0.3)
            glyph = rng.choice([20, 22, 24, 26, 28, 32])
            for _attempt in range(12):
                text, lines, tw, th, w, h = _plan_balloon(rng, lang, p, k, glyph, vertical)
                if w < pw - 30 and h < ph - 30:
                    break
                glyph = max(16, glyph - 3)
            else:
                continue
            # Reading order inside a panel: the first balloon sits on the starting side
            # (right for right-to-left), and higher unless the two share a row.
            start_side = (k == 0) == rtl
            x = (p[2] - w - rng.randint(10, 40)) if start_side else (p[0] + rng.randint(10, 40))
            if rng.random() < 0.2:      # breaks the panel border, as balloons often do
                x += 25 if start_side else -25
            if k == 0:
                y = p[1] + rng.randint(10, max(11, ph // 4))
            elif side_by_side:
                y = placed[0].y + rng.randint(-12, 12)
            else:
                y = max(placed[0].y + placed[0].h // 2 + 20, p[1] + ph // 3)
                y = min(y, p[3] - h - 10)
            if k == 1 and (y < placed[0].y - 12 or abs((x + w / 2) - (placed[0].x + placed[0].w / 2)) < w / 2):
                continue
            box = (x, y, w, h)
            if any(_overlaps(box, (q.x, q.y, q.w, q.h)) for q in placed):
                continue
            tx = x + (w - tw) // 2
            ty = y + (h - th) // 2
            b = Balloon(x, y, w, h, text.replace(" ", ""), vertical, glyph, (tx, ty, tw, th),
                        rng.choice(["ellipse", "ellipse", "round"]))
            _draw_balloon(draw, rng, b, p)
            placed.append(b)
            letterings.append((b, lines))
        balloons.extend(placed)
    clean = img.copy()
    cdraw = ImageDraw.Draw(img)
    for b, lines in letterings:
        key = (lang, b.glyph)
        if key not in font_cache:
            font_cache[key] = ImageFont.truetype(fonts[lang], b.glyph)
        _draw_lettering(cdraw, font_cache[key], lines, b.text_box[0], b.text_box[1], b.glyph, b.vertical)
    for i, b in enumerate(balloons):
        b.order = i
    degraded = index % 2 == 1
    if degraded:
        img, clean = _degrade(img, rng), _degrade(clean, random.Random(f"{seed}-{lang}-{index}-c"))
    return Page(f"{lang}-{index:02d}", lang, img.convert("RGB"), balloons, clean.convert("RGB"), degraded)


def _overlaps(a, b, pad=12):
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0]
                or a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


def _degrade(img, rng):
    """Paper tone, blur and noise, then a JPEG round trip, like a phone scan."""
    import io
    arr = np.asarray(img, dtype=np.float32)
    arr = arr * (rng.uniform(0.88, 0.95)) + rng.uniform(4, 10)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, 6, (img.height, img.width))
    out = Image.fromarray(np.clip(np.asarray(out, dtype=np.float32) + noise, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=70)
    buf.seek(0)
    return Image.open(buf).convert("L")


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def iou(a: dict, b: dict) -> float:
    ix = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def _inter(a: dict, b: dict) -> int:
    ix = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ix * iy


def match_score(pred: dict, b: Balloon) -> float:
    """How well a predicted box stands for balloon `b`: its IoU with the balloon,
    or, for a box hugging the lettering, a pass when it covers the text and stays
    inside the balloon. 0 when it is neither."""
    truth = {"x": b.x, "y": b.y, "w": b.w, "h": b.h}
    score = iou(pred, truth)
    if score >= MATCH_IOU:
        return score
    text = dict(zip("xywh", b.text_box))
    area = max(pred["w"] * pred["h"], 1)
    if (_inter(pred, text) >= MATCH_TEXT_COVER * text["w"] * text["h"]
            and _inter(pred, truth) >= 0.8 * area):
        return MATCH_IOU
    return 0.0


def match(preds: list, balloons: list) -> list:
    """Greedy one-to-one matching, best pair first: [(pred_index, balloon_index)].
    A second box on the same balloon stays unmatched, so it counts against precision."""
    pairs = sorted(((match_score(p, b), i, j) for i, p in enumerate(preds)
                    for j, b in enumerate(balloons)), reverse=True)
    used_p, used_b, out = set(), set(), []
    for score, i, j in pairs:
        if score < MATCH_IOU or i in used_p or j in used_b:
            continue
        used_p.add(i)
        used_b.add(j)
        out.append((i, j))
    return out


def normalize(text: str) -> str:
    """Letters and digits only, NFKC-folded: punctuation, spaces and line breaks
    are not what OCR quality is about here."""
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN")


def cer(hyp: str, ref: str) -> float:
    """Character error rate: edit distance over the reference length, capped at 1."""
    hyp, ref = normalize(hyp), normalize(ref)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return min(prev[-1] / len(ref), 1.0)


def pair_order_accuracy(order: list) -> float:
    """Share of balloon pairs read in the right order, given the predicted
    position of each balloon listed in true order. 1.0 for fewer than two."""
    n = len(order)
    if n < 2:
        return 1.0
    good = sum(1 for i in range(n) for j in range(i + 1, n) if order[i] < order[j])
    return good / (n * (n - 1) / 2)


def balloon_interior(page_size, b: Balloon, erode: int = 3) -> np.ndarray:
    """Boolean mask of the balloon's inside, from its known shape."""
    m = Image.new("L", page_size, 0)
    d = ImageDraw.Draw(m)
    box = (b.x + erode, b.y + erode, b.x + b.w - 1 - erode, b.y + b.h - 1 - erode)
    if b.shape == "ellipse":
        d.ellipse(box, fill=255)
    else:
        d.rounded_rectangle(box, radius=max(1, min(b.w, b.h) // 3 - erode), fill=255)
    return np.asarray(m) > 0


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def run_detect_ocr(page: Page, path: str, args) -> dict:
    """Detection, reading order and OCR through scanlate.detect_and_ocr_page."""
    import scanlate
    t0 = time.monotonic()
    bubbles, notes = scanlate.detect_and_ocr_page(
        path, page.lang, detect_backend=args.detect, ocr_backend=args.ocr,
        tesseract_cmd=args.tesseract_cmd)
    seconds = time.monotonic() - t0
    pairs = match(bubbles, page.balloons)
    matched = {j: i for i, j in pairs}
    ranks = sorted(bubbles, key=lambda b: b["reading_order"])
    rank_of = {id(b): r for r, b in enumerate(ranks)}
    order = [rank_of[id(bubbles[matched[j]])] for j in range(len(page.balloons)) if j in matched]
    cers = [cer(bubbles[i]["source_text"], page.balloons[j].text) for i, j in pairs]
    return {
        "truth": len(page.balloons), "predicted": len(bubbles), "matched": len(pairs),
        "cer_matched": cers, "order_pairs": pair_order_accuracy(order),
        "order_exact": order == sorted(order) and len(order) == len(page.balloons),
        "kinds": [bubbles[i].get("kind") for i, _j in pairs],
        "notes": [n[1] for n in notes], "seconds": seconds,
        "per_balloon": [bubbles[matched[j]] if j in matched else None
                        for j in range(len(page.balloons))],
    }


def run_oracle_ocr(page: Page, path: str, args) -> list:
    """OCR on the true balloon boxes, so OCR is scored apart from detection."""
    import scanlate
    from scanlate_regions import estimate_text_orientation
    from scanlate_detect import decode_page, page_gray
    image = decode_page(path)
    gray = page_gray(path, image)
    out = []
    for b in page.balloons:
        box = {"x": b.x, "y": b.y, "w": b.w, "h": b.h}
        box["orientation"] = estimate_text_orientation(gray, box, page.lang)
        text = scanlate.ocr_box_region(path, box, page.lang, backend=args.ocr,
                                       tesseract_cmd=args.tesseract_cmd, image=image)
        out.append((cer(text, b.text), b.vertical, box["orientation"] == ("vertical" if b.vertical else "horizontal")))
    return out


def run_typeset(page: Page, path: str, detected: list) -> dict:
    """Renders English into every true balloon with the app's render pass and
    measures, from the pixels: overflow past the balloon's inside, the English
    letter height against the source lettering, how much of the balloon the
    text block fills, and how much source ink the cleanup left behind.
    `detected`: the detect stage's region per balloon (or None), whose
    font_size and kind the render gets, as it would in the app."""
    import cv2
    import scanlate_render
    from scanlate_detect import decode_page
    rng = random.Random(f"typeset-{page.name}")
    bubbles = []
    for b, det in zip(page.balloons, detected):
        det = det or {}
        bubbles.append({"x": b.x, "y": b.y, "w": b.w, "h": b.h, "kind": det.get("kind") or "bubble",
                        "skip": False, "include_sfx": False, "font_category": "regular",
                        "translated_text": english_for(b.text, page.lang, rng),
                        "font_size": det.get("font_size") or 18})
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "out.png")
        scanlate_render.process_page(path, bubbles, out_path)
        out = np.asarray(Image.open(out_path).convert("L"))
    clean = np.asarray(page.clean.convert("L"))
    orig = np.asarray(page.image.convert("L"))
    new_ink = (out < 110) & (clean >= 160)
    overflow, heights, fills, art_lost = [], [], [], []
    for b in page.balloons:
        interior = balloon_interior(page.image.size, b)
        near = np.zeros_like(interior)
        near[max(0, b.y - 40):b.y + b.h + 40, max(0, b.x - 40):b.x + b.w + 40] = True
        overflow.append(int((new_ink & near & ~interior).sum()))
        # The balloon's outline and the art in its box's corners must survive.
        around = np.zeros_like(interior)
        around[max(0, b.y - 4):b.y + b.h + 4, max(0, b.x - 4):b.x + b.w + 4] = True
        around &= ~balloon_interior(page.image.size, b, erode=4)
        art = around & (clean < 110)
        art_lost.append(float((art & (out >= 160)).sum()) / max(int(art.sum()), 1))
        ink = (new_ink & interior).astype(np.uint8)
        n, _labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        hs = sorted(int(stats[i, cv2.CC_STAT_HEIGHT]) for i in range(1, n)
                    if stats[i, cv2.CC_STAT_AREA] >= 4)
        heights.append(hs[int(len(hs) * 0.9)] if hs else None)
        ys, xs = np.nonzero(ink)
        fills.append(((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)) / interior.sum()
                     if len(xs) else None)
    # Cleanup residue: erase only (no text drawn), then count surviving source ink.
    cleaned = decode_page(path)
    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
    for bub in bubbles:
        scanlate_render.erase_lettering(path, bub, cleaned, gray)
    cleaned_gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
    residue = []
    for b in page.balloons:
        tx, ty, tw, th = b.text_box
        src = (orig[ty:ty + th, tx:tx + tw] < 110) & (clean[ty:ty + th, tx:tx + tw] >= 160)
        left = src & (cleaned_gray[ty:ty + th, tx:tx + tw] < 110)
        residue.append(float(left.sum()) / max(int(src.sum()), 1))
    return {"overflow": overflow, "heights": heights, "glyphs": [b.glyph for b in page.balloons],
            "fills": fills, "residue": residue, "art_lost": art_lost}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _fmt(v, pct=False, digits=3):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v:.{digits}g}"


def summarize(results: list) -> dict:
    by = {}
    for r in results:
        by.setdefault(r["lang"], []).append(r)
    by["all"] = results
    out = {}
    for key, rs in by.items():
        d = [r["detect"] for r in rs]
        truth, pred, hit = sum(x["truth"] for x in d), sum(x["predicted"] for x in d), sum(x["matched"] for x in d)
        oracle = [c for r in rs for c in r["oracle"]]
        ts = [r["typeset"] for r in rs]
        sizes = [(s, g) for t in ts for s, g in zip(t["heights"], t["glyphs"]) if s]
        out[key] = {
            "pages": len(rs),
            "recall": hit / truth if truth else None,
            "precision": hit / pred if pred else None,
            "cer_detected": _mean([c for x in d for c in x["cer_matched"]]),
            "cer_oracle": _mean([c for c, _v, _o in oracle]),
            "cer_oracle_vertical": _mean([c for c, v, _o in oracle if v]),
            "cer_oracle_horizontal": _mean([c for c, v, _o in oracle if not v]),
            "orientation_right": _mean([1.0 if o else 0.0 for _c, _v, o in oracle]),
            "as_bubble": _mean([1.0 if k == "bubble" else 0.0 for x in d for k in x["kinds"]]),
            "order_pairs": _mean([x["order_pairs"] for x in d]),
            "order_exact_pages": _mean([1.0 if x["order_exact"] else 0.0 for x in d]),
            "overflow_balloons": _mean([1.0 if o > OVERFLOW_PIXELS else 0.0 for t in ts for o in t["overflow"]]),
            "letter_px_median": statistics.median([s for s, _ in sizes]) if sizes else None,
            "letter_vs_source": _mean([s / g for s, g in sizes]),
            "tiny_text": _mean([1.0 if s < TINY_LETTER_PX else 0.0 for s, _ in sizes]),
            "fill": _mean([f for t in ts for f in t["fills"]]),
            "residue": _mean([x for t in ts for x in t["residue"]]),
            "art_lost": _mean([x for t in ts for x in t["art_lost"]]),
            "seconds_per_page": _mean([x["seconds"] for x in d]),
        }
    return out


ROWS = [
    ("Bubble recall (found / real)", "recall", True),
    ("Bubble precision (real / found)", "precision", True),
    ("OCR character error, detected boxes", "cer_detected", True),
    ("OCR character error, true boxes", "cer_oracle", True),
    ("  vertical lettering", "cer_oracle_vertical", True),
    ("  horizontal lettering", "cer_oracle_horizontal", True),
    ("Text direction guessed right", "orientation_right", True),
    ("Found balloons classed as speech bubbles", "as_bubble", True),
    ("Reading order, pairs right", "order_pairs", True),
    ("Reading order, pages fully right", "order_exact_pages", True),
    ("Balloons with text overflow", "overflow_balloons", True),
    ("English letter height, median px", "letter_px_median", False),
    ("English letter height / source glyph", "letter_vs_source", False),
    (f"Balloons with English letters under {TINY_LETTER_PX} px", "tiny_text", True),
    ("Text block / balloon area", "fill", True),
    ("Source ink left after cleanup", "residue", True),
    ("Outline and art lost around balloons", "art_lost", True),
    ("Detect + OCR seconds per page", "seconds_per_page", False),
]


def report_markdown(summary: dict, meta: dict) -> str:
    keys = [k for k in ("all",) + LANGS if k in summary]
    lines = ["# Baihe Scanlate bench report", "",
             f"- Pages: {summary['all']['pages']} synthetic ({', '.join(meta['langs'])}), seed {meta['seed']}, "
             f"half degraded like a scan",
             f"- Detector: {meta['detect']} · OCR: {meta['ocr'] or 'auto for each language'}",
             f"- Overrides: {', '.join(f'{k}={v}' for k, v in meta['overrides'].items()) or 'none'}",
             "", "| Measure | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for label, key, pct in ROWS:
        lines.append(f"| {label} | " + " | ".join(_fmt(summary[k][key], pct) for k in keys) + " |")
    lines += ["", "Lower is better for character error, overflow, tiny text, residue and art lost; higher for the rest.",
              "Synthetic pages score the pipeline's mechanics, not a particular series.", ""]
    return "\n".join(lines)


def parse_overrides(pairs: list) -> dict:
    out = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep:
            raise BenchError(f"--set needs NAME=VALUE, got {pair!r}.")
        out[name.strip()] = value.strip()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pages", type=int, default=8, help="pages per language (default 8)")
    ap.add_argument("--langs", default="ja,zh,ko")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--detect", default="cv", choices=["cv", "ml", "auto"])
    ap.add_argument("--ocr", default=None, help="OCR backend for every language (default: the app's pick)")
    ap.add_argument("--tesseract-cmd", default=None)
    ap.add_argument("--font", default=None, help="one font file with CJK for every language")
    ap.add_argument("--set", action="append", dest="overrides", metavar="NAME=VALUE",
                    help="override a scanlate_tuning value for this run")
    ap.add_argument("--skip-ocr", action="store_true", help="score detection, order and typesetting only")
    ap.add_argument("--out", default=".", help="folder for the report, the JSON and sample pages")
    ap.add_argument("--save-pages", action="store_true", help="also write the generated pages")
    args = ap.parse_args(argv)
    try:
        langs = [x.strip() for x in args.langs.split(",") if x.strip()]
        bad = [x for x in langs if x not in LANGS]
        if bad:
            raise BenchError(f"Unknown language: {', '.join(bad)}.")
        overrides = parse_overrides(args.overrides)
        if overrides:
            import scanlate_tuning
            scanlate_tuning.apply_overrides(overrides)
        fonts = {lang: find_font(lang, args.font) for lang in langs}
    except (BenchError, ValueError) as exc:
        print(f"scanlate_bench: {exc}", file=sys.stderr)
        return 2
    if args.skip_ocr:
        import scanlate
        scanlate.ocr_box_region = lambda *a, **k: ""
    os.makedirs(args.out, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for lang in langs:
            for i in range(args.pages):
                page = make_page(i, lang, fonts, args.seed)
                path = os.path.join(tmp, f"{page.name}.png")
                page.image.save(path)
                if args.save_pages:
                    page.image.save(os.path.join(args.out, f"{page.name}.png"))
                detect = run_detect_ocr(page, path, args)
                r = {"page": page.name, "lang": lang, "degraded": page.degraded,
                     "detect": detect,
                     "oracle": run_oracle_ocr(page, path, args),
                     "typeset": run_typeset(page, path, detect.pop("per_balloon"))}
                results.append(r)
                print(f"{page.name}: found {r['detect']['matched']}/{r['detect']['truth']}, "
                      f"{r['detect']['predicted']} boxes", file=sys.stderr)
    summary = summarize(results)
    if args.skip_ocr:
        for row in summary.values():
            for key in [k for k in row if k.startswith("cer_")]:
                row[key] = None
    meta = {"langs": langs, "seed": args.seed, "detect": args.detect, "ocr": args.ocr,
            "overrides": overrides, "pages_per_lang": args.pages}
    Path(args.out, REPORT_NAME).write_text(report_markdown(summary, meta), encoding="utf-8")
    Path(args.out, JSON_NAME).write_text(json.dumps({"meta": meta, "summary": summary}, indent=2),
                                         encoding="utf-8")
    print(report_markdown(summary, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
