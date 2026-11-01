"""
sources/ai_extract_novel.py -- novel chapter extraction: block/link picks,
the deterministic tier, and the independent confidence checks on a result.
"""

from .ai_extract import (HEADING_TAGS, HIGH, MAX_NOVEL_CHARS, MEDIUM, Block, PageModel,
                         _DIALOGUE_START, _field, _number_check, _presence, chapter_num_str,
                         link_resemblance, squash)
from .generic_import import MIN_NOVEL_CHARS

_NEXT_WORDS = ("下一章", "下一页", "下一頁", "下一话", "下一話", "下章", "后一章", "next chapter",
               "next", "次へ", "次の話", "次話", "다음화", "다음")
_PREV_WORDS = ("上一章", "上一页", "上一頁", "上一话", "上一話", "上章", "前一章", "previous chapter",
               "prev", "previous", "前へ", "前の話", "前話", "이전화", "이전")
_TOC_WORDS = ("目录", "目錄", "章节目录", "table of contents", "contents", "index", "目次", "목록")


def _keyword_link(page: PageModel, words, current: str):
    """First short link whose text is one of `words` and that is shaped
    like another chapter; else the first keyword match (validation then
    decides)."""
    matches = []
    for l in page.links:
        t = l.text.strip().lower()
        if t and len(t) <= 20 and any(w in t for w in words):
            matches.append(l)
    for l in matches:
        if link_resemblance(l.url, current)[0] > 0:
            return l
    return matches[0] if matches else None


def _is_dialogue(text: str) -> bool:
    return bool(_DIALOGUE_START.match(text or ""))



def novel_data(page: PageModel, body_blocks, *, method: str, heading_ids=(), dialogue_ids=(),
               chapter_title=None, chapter_title_id=None, title=None, author=None,
               chapter_number=None, next_url=None, previous_url=None, toc=()) -> dict:
    """Builds the result from blocks already chosen (by id), copying their
    original text."""
    heading_ids, dialogue_ids = set(heading_ids), set(dialogue_ids)
    paras = []
    for b in body_blocks:
        kind = "heading" if (b.id in heading_ids or b.tag in HEADING_TAGS
                             or b.id == chapter_title_id) else "paragraph"
        paras.append({"block": b.id, "text": b.text, "kind": kind,
                      "dialogue": kind == "paragraph" and (b.id in dialogue_ids or _is_dialogue(b.text))})
    return {"content_type": "novel", "url": page.url, "method": method,
            "title": title, "author": author, "chapter_title": chapter_title,
            "chapter_title_block": chapter_title_id, "chapter_number": chapter_number,
            "paragraphs": paras, "content": "\n".join(p["text"] for p in paras),
            "next_url": next_url, "previous_url": previous_url,
            "toc_links": [{"text": l.text, "url": l.url} for l in toc],
            "rejected": {}}


def blocks_for_text(page: PageModel, text: str) -> list:
    """The page blocks a deterministic extractor's output came from, in
    page order (so a trafilatura/heuristic result gets real block ids and
    paragraph boundaries, and can seed a profile). [] unless every line
    maps to a block -- a partial mapping would silently drop text."""
    wanted = {squash(l) for l in (text or "").splitlines() if l.strip()}
    found = [b for b in page.blocks if squash(b.text) in wanted]
    if not wanted or {squash(b.text) for b in found} != wanted:
        return []
    return found


def deterministic_novel(page: PageModel) -> dict:
    """The deterministic tier (trafilatura, else the largest-text-
    block heuristic), reshaped into the same result as every other tier."""
    from .generic_import import extract_main_text
    text, method = extract_main_text(page.html, page.url)
    body = blocks_for_text(page, text)
    if not body and text.strip():
        # Couldn't map every line back to a block (e.g. trafilatura
        # normalized some); keep the extractor's own lines, all of them.
        body = [Block(id=f"t{i}", tag="p", text=l.strip())
                for i, l in enumerate(text.splitlines()) if l.strip()]
    heading = next((b for b in page.blocks if b.tag in ("h1", "h2") and b in body), None) or \
        next((b for b in page.blocks if b.tag in ("h1", "h2")), None)
    chapter_title = heading.text if heading else (page.page_title or None)
    nxt = _keyword_link(page, _NEXT_WORDS, page.url)
    prv = _keyword_link(page, _PREV_WORDS, page.url)
    toc = [l for l in page.links if l.text.strip().lower() in _TOC_WORDS][:3]
    return novel_data(page, body, method=method, chapter_title=chapter_title,
                      chapter_title_id=heading.id if heading else None,
                      chapter_number=chapter_num_str(chapter_title),
                      next_url=nxt.url if nxt else None, previous_url=prv.url if prv else None,
                      toc=toc)


NOVEL_PROMPT = (
    "Below is one web page, split into numbered text blocks [b0], [b1], ... in page order "
    "(each tagged with the HTML element it sits in) and numbered links [L0], [L1], .... "
    "It should contain one chapter of a novel. Work out which blocks ARE the chapter. "
    "Do NOT rewrite, summarize, translate or correct anything, and don't copy text out -- "
    "answer only with block and link ids; the app copies the original text itself.\n\n"
    "Return ONLY a JSON object with these keys (null, or [] for lists, for anything you "
    "can't identify confidently -- never guess):\n"
    '{"title_block": id of the block holding the book/work title, "author_block": id, '
    '"chapter_title_block": id, "chapter_number": "the chapter number exactly as written in '
    'the chapter title", "body_range": [first body block id, last body block id], '
    '"exclude_blocks": [ids inside that range that are NOT chapter text: ads, nav, notices, '
    'comments], "heading_blocks": [ids of headings inside the chapter], '
    '"dialogue_blocks": [ids of spoken-dialogue paragraphs], "next_link": link id of the '
    'next chapter, "previous_link": link id of the previous chapter, "toc_links": [link ids '
    'of the table of contents / chapter list], "confidence": {"chapter_title": 0-1, '
    '"content": 0-1, "next_url": 0-1, "previous_url": 0-1}}. '
    "No preamble, no markdown fences.\n\n")


def novel_from_picks(page: PageModel, picks: dict) -> dict:
    """Turns the model's id-keyed answer into a result. Every id is looked
    up by name; unknown ids are ignored -- nothing is matched by position."""
    picks = picks if isinstance(picks, dict) else {}
    index = {b.id: i for i, b in enumerate(page.blocks)}
    body_ids = [i for i in (picks.get("body_blocks") or []) if i in index]
    rng = picks.get("body_range")
    if not body_ids and isinstance(rng, list) and len(rng) == 2 and all(r in index for r in rng):
        lo, hi = sorted((index[rng[0]], index[rng[1]]))
        body_ids = [b.id for b in page.blocks[lo:hi + 1]]
    exclude = set(picks.get("exclude_blocks") or [])
    body = sorted((page.blocks[index[i]] for i in set(body_ids) - exclude),
                  key=lambda b: index[b.id])

    def text_of(key):
        b = page.block(picks.get(key)) if isinstance(picks.get(key), str) else None
        return (b.text if b else None), (b.id if b else None)

    chapter_title, ct_id = text_of("chapter_title_block")
    title, _ = text_of("title_block")
    author, _ = text_of("author_block")
    number = picks.get("chapter_number")
    number = str(number).strip() if isinstance(number, (str, int, float)) and str(number).strip() else None

    def link_of(key):
        l = page.link(picks.get(key)) if isinstance(picks.get(key), str) else None
        return l.url if l else None

    toc = [page.link(i) for i in (picks.get("toc_links") or []) if isinstance(i, str) and page.link(i)]
    return novel_data(page, body, method="llm", heading_ids=picks.get("heading_blocks") or [],
                      dialogue_ids=picks.get("dialogue_blocks") or [], chapter_title=chapter_title,
                      chapter_title_id=ct_id, title=title, author=author, chapter_number=number,
                      next_url=link_of("next_link"), previous_url=link_of("previous_link"), toc=toc)



def validate_novel(data: dict, page: PageModel, model_conf: dict | None = None) -> dict:
    """Independent checks on a novel result; fills data["confidence"],
    data["overall"], data["valid"], data["problems"]. Fields that fail are
    set to None (the rejected value kept under data["rejected"])."""
    mc = model_conf if isinstance(model_conf, dict) else {}
    conf = {}
    paras = data.get("paragraphs") or []
    content = (data.get("content") or "").strip()

    checks, score = [], 1.0
    n = len(content)
    if n == 0:
        score = 0.0
        checks.append("no text was extracted")
    elif n < MIN_NOVEL_CHARS:
        score = 0.0
        checks.append(f"only {n} characters -- too short to be a chapter")
    elif n > MAX_NOVEL_CHARS:
        score = 0.0
        checks.append(f"{n:,} characters -- implausibly long for one chapter "
                      "(probably the whole page or a whole book)")
    else:
        texts = [p["text"] for p in paras]
        if len(texts) < 3:
            score -= 0.35
            checks.append(f"only {len(texts)} paragraph(s)")
        longish = [squash(t) for t in texts if len(squash(t)) >= 4]
        if longish:
            dup = 1 - len(set(longish)) / len(longish)
            if dup > 0.3:
                score -= 0.6
                checks.append(f"{dup:.0%} of the paragraphs are repeats (looks like navigation)")
        if texts:
            short = sum(1 for t in texts if len(squash(t)) <= 12) / len(texts)
            if short > 0.6:
                score -= 0.4
                checks.append(f"{short:.0%} of it is short fragments (menus/links, not prose)")
            link_texts = {squash(l.text) for l in page.links if l.text}
            linky = sum(1 for t in texts if squash(t) in link_texts) / len(texts)
            if linky > 0.3:
                score -= 0.4
                checks.append(f"{linky:.0%} of the paragraphs are link text")
            sample = texts[:: max(1, len(texts) // 20)]
            present = sum(1 for t in sample if page.contains(t)) / len(sample)
            if present < 0.8:
                score = 0.0
                checks.append("the extracted text isn't actually on the page")
    conf["content"] = _field(score, checks, mc.get("content"))

    for key in ("chapter_title", "title", "author"):
        c = []
        s = _presence(page, data.get(key), c)
        conf[key] = _field(s, c, mc.get(key))
        if s < 0.5 and data.get(key) is not None:
            data["rejected"][key] = data[key]
            data[key] = None

    c = []
    s = _number_check(data.get("chapter_number"), data.get("chapter_title"), page.url, c)
    conf["chapter_number"] = _field(s, c, mc.get("chapter_number"))
    if s == 0 and data.get("chapter_number") is not None:
        data["rejected"]["chapter_number"] = data["chapter_number"]
        data["chapter_number"] = None

    for key in ("next_url", "previous_url"):
        s, why = link_resemblance(data.get(key), page.url)
        conf[key] = _field(s, [why] if s < 0.8 else [], mc.get(key))
        if s == 0 and data.get(key):
            data["rejected"][key] = data[key]
            data[key] = None
    if data.get("next_url") and data.get("next_url") == data.get("previous_url"):
        conf["previous_url"] = _field(0, ["same link as 'next'"])
        data["rejected"]["previous_url"] = data["previous_url"]
        data["previous_url"] = None

    data["confidence"] = conf
    data["overall"] = {"score": conf["content"]["score"], "bucket": conf["content"]["bucket"]}
    data["valid"] = conf["content"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["content"]["checks"]) + [
        f"{k.replace('_', ' ')} rejected: {conf[k]['checks'][0]}" for k in data["rejected"]
        if conf.get(k, {}).get("checks")]
    return data
