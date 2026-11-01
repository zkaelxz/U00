"""
sources/ai_extract_comic.py -- comic chapter extraction: image candidates,
the model's id-keyed roles, duplicate removal and the independent checks.
"""

import json
import re

from .ai_extract import (COMIC_ROLES, HEADING_TAGS, HIGH, MAX_PAGES, MEDIUM, PageModel, _field,
                         _looks_thumb, _number_check, _presence, chapter_num_str, dedup_key,
                         file_number, well_formed)
from .generic_import import MIN_PAGE_HEIGHT, MIN_PAGE_WIDTH

_CHROME_HINT = re.compile(r"cover|banner|\bads?\b|advert|sponsor|promo|recommend|related|thumb|"
                          r"logo|avatar|icon|share|qrcode|广告|推荐", re.I)


def comic_candidates(html: str, url: str) -> list:
    """Every candidate the deterministic pass surfaces: <img>/<source>
    (incl. srcset, data-src and other lazy attributes, <picture>) plus
    image URLs listed in inline scripts/JSON manifests."""
    from .generic_import import image_candidates, manifest_candidates
    cands = image_candidates(html, url)
    extra = manifest_candidates(html, url, skip={c.url for c in cands})
    for c in extra:
        c.order = len(cands)
        cands.append(c)
    return cands


def _desc(c, cid) -> dict:
    d = {"id": cid, "url": c.url, "attr": c.attr or "src", "context": c.hint}
    if c.width_attr:
        d["w"] = c.width_attr
    if c.height_attr:
        d["h"] = c.height_attr
    return d


def comic_payload(page: PageModel, candidates) -> str:
    heads = [b.text for b in page.blocks if b.tag in HEADING_TAGS][:4]
    lines = [f"URL: {page.url}", f"PAGE TITLE: {page.page_title}",
             "HEADINGS: " + " / ".join(heads), "IMAGES:"]
    for i, c in enumerate(candidates):
        lines.append(json.dumps(_desc(c, f"i{i}"), ensure_ascii=False))
    return "\n".join(lines)


COMIC_PROMPT = (
    "Below are the candidate images found on one comic-chapter web page, in page order. Each "
    "has an id, the attribute it came from (src, srcset, data-src and other lazy-load "
    "attributes, or \"manifest\" = listed inside a script/JSON block), its declared "
    "width/height if any, and context (alt text, the id/class chain of its container, a link "
    "it sits inside). Decide what each one is. Roles: \"content\" (an actual page of this "
    "chapter), \"cover\", \"thumbnail\" (small preview), \"ad\", \"recommendation\" (promo for "
    "another work), \"icon\" (logo/button/avatar), \"duplicate\" (the same page as another "
    "candidate, e.g. a low-res or lazy placeholder version), \"other\".\n\n"
    "Return ONLY a JSON object: {\"title\": work title exactly as written on the page or null, "
    "\"chapter_title\": exactly as written or null, \"chapter_number\": as written or null, "
    "\"images\": [{\"id\": \"i0\", \"role\": \"...\", \"page\": reading-order position "
    "starting at 1 for content pages (null otherwise), \"confidence\": 0-1}]}. Use only the "
    "ids given -- never invent URLs. null for anything you can't identify confidently. No "
    "preamble, no markdown fences.\n\n")


def comic_data(page: PageModel, candidates, roles: dict, *, method: str, order: dict | None = None,
               reasons: dict | None = None, confidences: dict | None = None, title=None, chapter_title=None,
               chapter_number=None) -> dict:
    """Result in the roadmap's shape. `roles`, `order`, `reasons` and
    `confidences` are keyed by candidate URL (never list position)."""
    order, reasons, confidences = order or {}, reasons or {}, confidences or {}
    content = [c for c in candidates if roles.get(c.url) == "content"]
    content.sort(key=lambda c: (order.get(c.url) if isinstance(order.get(c.url), (int, float))
                                else 10 ** 6, c.order))
    pages = [{"index": i, "resource_url": c.url, "role": "content",
              "confidence": confidences.get(c.url), "source_attr": c.attr or "src"}
             for i, c in enumerate(content)]
    for c in candidates:
        if roles.get(c.url) != "content":
            pages.append({"index": None, "resource_url": c.url, "role": roles.get(c.url) or "other",
                          "confidence": confidences.get(c.url), "source_attr": c.attr or "src",
                          "reason": reasons.get(c.url, "")})
    return {"content_type": "comic", "url": page.url, "method": method, "title": title,
            "chapter_title": chapter_title, "chapter_number": chapter_number, "pages": pages,
            "rejected": {}}


def dedupe_content(candidates, roles: dict, reasons: dict):
    """Independent duplicate pass: two content candidates that are the same
    picture at another size / via another attribute keep only the fuller
    one. Applied whatever the model said."""
    by_key = {}
    for c in candidates:
        if roles.get(c.url) != "content":
            continue
        by_key.setdefault(dedup_key(c.url), []).append(c)
    for group in by_key.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda c: (_looks_thumb(c.url), c.attr == "manifest", c.order))
        for c in group[1:]:
            roles[c.url] = "duplicate"
            reasons[c.url] = f"same picture as {group[0].url}"
    for c in candidates:
        if roles.get(c.url) == "content" and c.width_attr and c.height_attr and \
                (c.width_attr < MIN_PAGE_WIDTH or c.height_attr < MIN_PAGE_HEIGHT):
            roles[c.url] = "thumbnail"
            reasons[c.url] = f"declared size {c.width_attr}x{c.height_attr} is too small for a page"


def comic_from_picks(page: PageModel, candidates, picks: dict) -> dict:
    picks = picks if isinstance(picks, dict) else {}
    by_id = {f"i{i}": c for i, c in enumerate(candidates)}
    roles, order, conf, reasons = {}, {}, {}, {}
    for item in picks.get("images") or []:
        if not isinstance(item, dict) or item.get("id") not in by_id:
            continue
        c = by_id[item["id"]]
        role = item.get("role") if item.get("role") in COMIC_ROLES else "other"
        roles[c.url] = role
        if isinstance(item.get("page"), (int, float)) and not isinstance(item.get("page"), bool):
            order[c.url] = item["page"]
        if isinstance(item.get("confidence"), (int, float)):
            conf[c.url] = float(item["confidence"])
    for c in candidates:
        if c.url not in roles:
            roles[c.url] = "other"
            reasons[c.url] = "the model didn't classify it"
    dedupe_content(candidates, roles, reasons)

    def text(key):
        v = picks.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else None

    return comic_data(page, candidates, roles, method="llm", order=order, reasons=reasons,
                      confidences=conf, title=text("title"), chapter_title=text("chapter_title"),
                      chapter_number=text("chapter_number"))


_REJECT_ROLE = (("too small", "thumbnail"), ("other chapters", "ad"), ("duplicate", "duplicate"),
                ("third-party", "ad"), ("shape", "other"), ("readable", "other"),
                ("download", "other"))


def comic_from_filter(page: PageModel, candidates, kept, rejected, method="filter") -> dict:
    """The existing content-vs-chrome filter's verdict in the same shape."""
    roles, reasons = {}, {}
    for c in kept:
        roles[c.url] = "content"
    for c in rejected:
        roles[c.url] = next((r for k, r in _REJECT_ROLE if k in c.reject_reason), "other")
        reasons[c.url] = c.reject_reason
    heading = next((b.text for b in page.blocks if b.tag in ("h1", "h2")), None)
    return comic_data(page, candidates, roles, method=method,
                      order={c.url: i for i, c in enumerate(kept)}, reasons=reasons,
                      chapter_title=heading or page.page_title or None,
                      chapter_number=chapter_num_str(heading or page.page_title))


def comic_needs_review(data: dict, candidates) -> list:
    """Why a deterministic comic result is ambiguous (empty = it isn't)."""
    why = []
    if not data.get("valid"):
        why.extend(data.get("problems") or ["no usable pages"])
    by_url = {c.url: c for c in candidates}
    chrome = [p["resource_url"] for p in data["pages"] if p["role"] == "content"
              and _CHROME_HINT.search((by_url.get(p["resource_url"]).hint if
                                       by_url.get(p["resource_url"]) else "") + " " +
                                      p["resource_url"].rsplit("/", 1)[-1])]
    if chrome:
        why.append(f"{len(chrome)} kept image(s) look like covers/ads/thumbnails by their context")
    return why


def validate_comic(data: dict, page: PageModel, measured: dict | None = None, model_conf: dict | None = None) -> dict:
    """Independent checks on a comic result. `measured` maps resource URL
    -> {"width", "height", "sha256"} for images the downloader has
    fetched (never fetched here)."""
    measured = measured or {}
    mc = model_conf if isinstance(model_conf, dict) else {}
    conf = {}
    pages = [p for p in data["pages"] if p["role"] == "content"]
    checks, score = [], 1.0
    bad = [p for p in pages if not well_formed(p["resource_url"])]
    if bad:
        for p in bad:
            p["role"], p["index"], p["reason"] = "other", None, "not a well-formed URL"
        pages = [p for p in pages if p not in bad]
        for i, p in enumerate(pages):
            p["index"] = i
        checks.append(f"{len(bad)} page URL(s) weren't well-formed and were dropped")
        score -= 0.2
    n = len(pages)
    if n == 0:
        score = 0.0
        checks.append("no content pages identified")
    else:
        keys = [dedup_key(p["resource_url"]) for p in pages]
        shas = [measured.get(p["resource_url"], {}).get("sha256") for p in pages]
        known = [s for s in shas if s]
        dup = max(1 - len(set(keys)) / len(keys),
                  (1 - len(set(known)) / len(known)) if known else 0.0)
        if dup > 0.25:
            score = 0.0
            checks.append(f"{dup:.0%} of the pages are the same image -- not a real page set")
        elif dup > 0:
            score -= 0.3
            checks.append(f"{dup:.0%} of the pages repeat")
        dims = [(measured[p["resource_url"]].get("width", 0), measured[p["resource_url"]].get("height", 0))
                for p in pages if p["resource_url"] in measured]
        small = sum(1 for w, h in dims if w < MIN_PAGE_WIDTH or h < MIN_PAGE_HEIGHT)
        if dims and small / len(dims) > 0.3:
            score -= 0.5
            checks.append(f"{small} page(s) are too small to be pages")
        if n > MAX_PAGES:
            score -= 0.5
            checks.append(f"{n} pages -- implausibly many for one chapter")
    conf["page_images"] = _field(score, checks, mc.get("page_images"))

    oc, os_ = [], 0.0
    if n:
        os_ = 0.9
        nums = [file_number(p["resource_url"]) for p in pages]
        if all(x is not None for x in nums) and len(set(nums)) == n and n > 1:
            if nums == sorted(nums):
                os_ = 0.95
            else:
                os_ = 0.4
                oc.append("page order disagrees with the numbers in the file names")
    else:
        oc.append("no pages to order")
    conf["page_order"] = _field(os_, oc, mc.get("page_order"))

    for key in ("chapter_title", "title"):
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

    data["confidence"] = conf
    data["overall"] = {"score": conf["page_images"]["score"], "bucket": conf["page_images"]["bucket"]}
    data["valid"] = conf["page_images"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["page_images"]["checks"]) + list(conf["page_order"]["checks"])
    return data
