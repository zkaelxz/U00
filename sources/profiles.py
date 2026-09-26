"""
sources/profiles.py -- per-domain extraction profiles (roadmap Step 23g
item 4).

Once a site's layout has been worked out -- by the AI-assisted fallback,
or by the person correcting a result on the Review Extraction screen --
what worked is kept as plain rules (CSS selectors, URL patterns), so the
next chapter from the same site goes straight to them: no LLM call.

Stored as one JSON file per domain, `<library>/source_profiles/<domain>.json`
-- the roadmap's `source_profiles/<domain>` layout, rooted under
db.LIBRARY_DIR at call time (like sources/store.py) so the tests'
`isolated_db` fixture redirects it. JSON rather than YAML: the app has no
YAML dependency, and this file is written by the app, not by hand.

Rules the module enforces, not just documents:

  * Nothing is saved without passing validation (the profile is re-run
    against the real page and the result goes through the same
    independent checks as any extraction).
  * Below HIGH confidence, saving needs the person's explicit approval.
  * Versions are append-only. A new version supersedes the active one;
    the old one stays on disk and can be made active again (rollback).
    A profile that stops working is marked as failing, never deleted or
    overwritten.
"""

import json
import os
import re
import time
from urllib.parse import urljoin, urlsplit

import db

from . import ai_extract as ax

FORMAT_VERSION = 1


class ProfileRejected(Exception):
    """A profile that didn't validate, or needs approval it doesn't have."""


def profiles_dir() -> str:
    return os.path.join(db.LIBRARY_DIR, "source_profiles")


def domain_of(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _path(domain: str) -> str:
    safe = re.sub(r"[^a-z0-9.\-]", "_", (domain or "unknown").lower())
    return os.path.join(profiles_dir(), f"{safe}.json")


def load(domain: str) -> dict:
    try:
        with open(_path(domain), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        rec = {"domain": domain, "format": FORMAT_VERSION, "active": {}, "versions": []}
    rec.setdefault("active", {})
    rec.setdefault("versions", [])
    return rec


def _write(rec: dict):
    os.makedirs(profiles_dir(), exist_ok=True)
    path = _path(rec["domain"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_domains() -> list:
    try:
        names = sorted(os.listdir(profiles_dir()))
    except OSError:
        return []
    return [n[:-5] for n in names if n.endswith(".json")]


def versions(domain: str, kind: str = None) -> list:
    return [v for v in load(domain)["versions"] if kind is None or v["kind"] == kind]


def active(domain: str, kind: str):
    rec = load(domain)
    num = rec["active"].get(kind)
    return next((v for v in rec["versions"] if v["version"] == num and v["kind"] == kind), None)


def save_version(domain: str, kind: str, rules: dict, validation: dict, origin: str,
                 approved: bool = False, note: str = "") -> dict:
    """Adds a new version and makes it active. `validation` is the result
    of re-running these rules on a real page (validate_* output).
    Raises ProfileRejected rather than saving something unvalidated, or a
    below-HIGH profile nobody approved."""
    if not rules:
        raise ProfileRejected("There's no profile to save.")
    if not validation or not validation.get("valid"):
        why = "; ".join((validation or {}).get("problems") or []) or "it didn't validate"
        raise ProfileRejected(f"Not saved -- re-running this profile on the page failed its checks: {why}")
    bucket = (validation.get("overall") or {}).get("bucket")
    if bucket != ax.HIGH and not approved:
        raise ProfileRejected(f"Confidence is {bucket} -- saving needs your approval.")
    rec = load(domain)
    number = max((v["version"] for v in rec["versions"]), default=0) + 1
    previous = rec["active"].get(kind)
    for v in rec["versions"]:
        if v["kind"] == kind and v["version"] == previous:
            v["status"] = "superseded"
    entry = {"version": number, "kind": kind, "rules": rules, "origin": origin,
             "created_at": time.time(), "status": "active", "approved": bool(approved),
             "replaces": previous, "note": note, "failures": 0, "last_failure": None,
             "last_used": None,
             "validation": {"overall": validation.get("overall"),
                            "problems": validation.get("problems") or [],
                            "fields": {k: f["bucket"] for k, f in
                                       (validation.get("confidence") or {}).items()}}}
    rec["versions"].append(entry)
    rec["active"][kind] = number
    _write(rec)
    return entry


def rollback(domain: str, kind: str, version: int) -> dict:
    """Makes an earlier version active again. Nothing is deleted."""
    rec = load(domain)
    target = next((v for v in rec["versions"] if v["kind"] == kind and v["version"] == version), None)
    if target is None:
        raise ProfileRejected(f"{domain} has no {kind} profile version {version}.")
    for v in rec["versions"]:
        if v["kind"] == kind and v["status"] == "active":
            v["status"] = "superseded"
    target["status"] = "active"
    rec["active"][kind] = version
    _write(rec)
    return target


def record_use(domain: str, kind: str, version: int, ok: bool, reason: str = ""):
    """Notes a success or a failed validation on a version. A failing
    version stays on disk (and stays active until a validated replacement
    exists) -- the person can see it failed and roll back or forward."""
    rec = load(domain)
    for v in rec["versions"]:
        if v["kind"] == kind and v["version"] == version:
            v["last_used"] = time.time()
            if ok:
                v["failures"] = 0
            else:
                v["failures"] = v.get("failures", 0) + 1
                v["last_failure"] = {"at": time.time(), "reason": reason}
    _write(rec)


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------

_SAFE_IDENT = re.compile(r"-?[A-Za-z_][\w\-]*")


def _inside(el, ancestor) -> bool:
    # Identity, not ==: bs4 Tags compare equal by markup.
    return el is ancestor or any(p is ancestor for p in el.parents)


def _root(el):
    while el is not None and el.parent is not None:
        el = el.parent
    return el


def common_ancestor(els):
    els = [e for e in els if e is not None]
    if not els:
        return None
    paths = [list(reversed([e] + list(e.parents))) for e in els]
    found = None
    for nodes in zip(*paths):
        if all(n is nodes[0] for n in nodes):
            found = nodes[0]
        else:
            break
    return found


def css_for(el, soup=None, allow_multi: bool = False, _depth: int = 0):
    """A CSS selector that picks out `el` (first match, and the only match
    unless allow_multi). Prefers id, then classes, then a short
    nth-of-type path. None if no stable selector exists."""
    if el is None or getattr(el, "name", None) in (None, "[document]"):
        return None
    soup = soup or _root(el)
    if el.name in ("html", "body"):
        return el.name
    options = []
    if el.get("id") and _SAFE_IDENT.fullmatch(el["id"]) and not re.search(r"\d{4,}", el["id"]):
        options.append(f"{el.name}#{el['id']}")
    classes = [c for c in (el.get("class") or []) if _SAFE_IDENT.fullmatch(c)
               and not re.search(r"\d{3,}", c)]
    if classes:
        options.append(el.name + "".join("." + c for c in classes))
        options.extend(f"{el.name}.{c}" for c in classes)
    for sel in options:
        try:
            found = soup.select(sel)
        except Exception:
            continue
        if found and found[0] is el and (allow_multi or len(found) == 1):
            return sel
    if _depth >= 6 or el.parent is None or el.parent.name == "[document]":
        return None
    parent_sel = css_for(el.parent, soup, _depth=_depth + 1)
    if not parent_sel:
        return None
    same = [c for c in el.parent.find_all(el.name, recursive=False)]
    k = next(i for i, c in enumerate(same, 1) if c is el)
    sel = f"{parent_sel} > {el.name}:nth-of-type({k})"
    try:
        found = soup.select(sel)
    except Exception:
        return None
    return sel if found and found[0] is el else None


def _select_one(soup, sel):
    if not sel:
        return None
    try:
        return soup.select_one(sel)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Novel profiles
# ---------------------------------------------------------------------------

def _link_rule(page, url):
    if not url:
        return None
    link = next((l for l in page.links if l.url == url), None)
    if link is None:
        return None
    return {"selector": css_for(link.el, page.soup), "text": link.text}


def infer_novel_rules(page, data: dict):
    """Rules that reproduce `data` on this page and, being selectors
    rather than text, on the site's other chapters."""
    blocks = [page.block(p.get("block")) for p in data.get("paragraphs") or []]
    blocks = [b for b in blocks if b is not None]
    if not blocks:
        return None
    container = common_ancestor([b.el for b in blocks])
    content_sel = css_for(container, page.soup)
    if not content_sel:
        return None
    body_ids = {b.id for b in blocks}
    exclude = []
    for b in page.blocks:
        if b.id in body_ids or b.el is container or not _inside(b.el, container):
            continue
        sel = css_for(b.el, page.soup, allow_multi=True)
        if sel and sel not in exclude and not any(
                _inside(x.el, e) for x in blocks for e in page.soup.select(sel)):
            exclude.append(sel)
    title_block = page.block(data.get("chapter_title_block")) if data.get("chapter_title_block") else None
    return {"content_selector": content_sel, "exclude_selectors": exclude,
            "title_selector": css_for(title_block.el, page.soup) if title_block else None,
            "next": _link_rule(page, data.get("next_url")),
            "previous": _link_rule(page, data.get("previous_url")),
            "number_from": "title"}


def _resolve_link(page, rule):
    if not rule:
        return None
    el = _select_one(page.soup, rule.get("selector"))
    if el is not None and el.get("href"):
        return urljoin(page.url, el["href"].strip())
    text = (rule.get("text") or "").strip()
    if text:
        hit = next((l for l in page.links if l.text.strip() == text), None)
        if hit:
            return hit.url
    return None


def apply_novel_rules(page, rules: dict) -> tuple:
    """(data, "") or (None, plain-language reason it couldn't be applied)."""
    container = _select_one(page.soup, rules.get("content_selector"))
    if container is None:
        return None, (f"the saved content container ({rules.get('content_selector')}) "
                      "isn't on this page")
    excluded = []
    for sel in rules.get("exclude_selectors") or []:
        try:
            excluded.extend(page.soup.select(sel))
        except Exception:
            continue
    body = [b for b in page.blocks if _inside(b.el, container)
            and not any(_inside(b.el, x) for x in excluded)]
    title_el = _select_one(page.soup, rules.get("title_selector"))
    title_block = next((b for b in page.blocks if title_el is not None and b.el is title_el), None)
    chapter_title = title_el.get_text(" ", strip=True) if title_el is not None else None
    number = None
    if rules.get("number_from") == "url":
        nums = re.findall(r"\d+", urlsplit(page.url).path)
        number = str(int(nums[-1])) if nums else None
    else:
        number = ax._chapter_num_str(chapter_title)
    data = ax.novel_data(page, body, method="profile", chapter_title=chapter_title or None,
                         chapter_title_id=title_block.id if title_block else None,
                         chapter_number=number, next_url=_resolve_link(page, rules.get("next")),
                         previous_url=_resolve_link(page, rules.get("previous")))
    return data, ""


def container_options(page, limit: int = 8) -> list:
    """[(selector, characters, preview)] for the Review screen's
    "content container" choice, biggest text holders first."""
    sizes, els = {}, {}
    for b in page.blocks:
        node = b.el
        for _ in range(3):
            if node is None or node.name in (None, "[document]"):
                break
            sizes[id(node)] = sizes.get(id(node), 0) + len(b.text)
            els[id(node)] = node
            node = node.parent
    out, seen = [], set()
    for key in sorted(sizes, key=sizes.get, reverse=True):
        sel = css_for(els[key], page.soup)
        if not sel or sel in seen:
            continue
        seen.add(sel)
        out.append((sel, sizes[key], els[key].get_text(" ", strip=True)[:80]))
        if len(out) >= limit:
            break
    return out


def exclusion_options(page, content_selector: str, limit: int = 15) -> list:
    """[(selector, preview)] of named elements inside the container a
    person might want to drop (nav bars, comment boxes, ad slots)."""
    container = _select_one(page.soup, content_selector)
    if container is None:
        return []
    out, seen = [], set()
    for el in container.find_all(True):
        if not (el.get("id") or el.get("class")) or not el.get_text(strip=True):
            continue
        sel = css_for(el, page.soup, allow_multi=True)
        if sel and sel not in seen:
            seen.add(sel)
            out.append((sel, el.get_text(" ", strip=True)[:60]))
        if len(out) >= limit:
            break
    return out


def novel_rules_from_choices(page, content_selector: str, exclude_selectors=(),
                             title_block_id: str = None, next_link_id: str = None,
                             previous_link_id: str = None, number_from: str = "title") -> dict:
    """The Review screen's corrections, as a profile. Corrections are
    structure (which element, which link) -- never edits to the text."""
    tb = page.block(title_block_id) if title_block_id else None
    nl = page.link(next_link_id) if next_link_id else None
    pl = page.link(previous_link_id) if previous_link_id else None
    return {"content_selector": content_selector, "exclude_selectors": list(exclude_selectors),
            "title_selector": css_for(tb.el, page.soup) if tb else None,
            "next": {"selector": css_for(nl.el, page.soup), "text": nl.text} if nl else None,
            "previous": {"selector": css_for(pl.el, page.soup), "text": pl.text} if pl else None,
            "number_from": number_from}


# ---------------------------------------------------------------------------
# Comic profiles
# ---------------------------------------------------------------------------

def _name_pattern(url: str) -> str:
    return re.sub(r"\d+", "#", urlsplit(url or "").path.rsplit("/", 1)[-1].lower())


def infer_comic_rules(candidates, data: dict):
    by_url = {c.url: c for c in candidates}
    content = [by_url[p["resource_url"]] for p in data["pages"]
               if p["role"] == "content" and p["resource_url"] in by_url]
    if not content:
        return None
    content.sort(key=lambda c: next(p["index"] for p in data["pages"] if p["resource_url"] == c.url))
    tags = [c.tag for c in content if c.tag is not None]
    container_sel = None
    if tags:
        container_sel = css_for(common_ancestor(tags), _root(tags[0]))
    skeletons = sorted({ax.url_skeleton(c.url) for c in content})
    page_names = {_name_pattern(c.url) for c in content}
    excluded_urls, excluded_names = [], []
    for p in data["pages"]:
        if p["role"] in ("content", "duplicate"):
            continue
        if ax.url_skeleton(p["resource_url"]) in skeletons:
            excluded_urls.append(p["resource_url"])
            name = _name_pattern(p["resource_url"])
            if name not in page_names and name not in excluded_names:
                excluded_names.append(name)
    nums = [ax._file_number(c.url) for c in content]
    doc_order = sorted(content, key=lambda c: c.order)
    order_by = "document"
    if content != doc_order and all(n is not None for n in nums) and nums == sorted(nums) and \
            len(set(nums)) == len(nums):
        order_by = "filename"
    return {"container_selector": container_sel,
            "use_manifest": any(c.attr == "manifest" for c in content),
            "page_skeletons": skeletons, "excluded_urls": excluded_urls,
            "excluded_names": excluded_names, "order_by": order_by}


def apply_comic_rules(candidates, rules: dict) -> tuple:
    """(content candidates in reading order, "") or (None, reason). Pure:
    picks from candidates the deterministic pass already surfaced."""
    container = None
    if rules.get("container_selector"):
        tagged = [c for c in candidates if c.tag is not None]
        root = _root(tagged[0].tag) if tagged else None
        container = _select_one(root, rules["container_selector"]) if root is not None else None
        if container is None and not rules.get("use_manifest"):
            return None, (f"the saved page container ({rules['container_selector']}) "
                          "isn't on this page")
    skeletons = set(rules.get("page_skeletons") or [])
    excluded = set(rules.get("excluded_urls") or [])
    bad_names = set(rules.get("excluded_names") or [])
    picked = []
    for c in candidates:
        if container is not None:
            in_place = (c.tag is not None and _inside(c.tag, container)) or \
                (rules.get("use_manifest") and c.attr == "manifest")
        elif rules.get("container_selector"):
            in_place = c.attr == "manifest"
        else:
            in_place = True
        if not in_place or c.url in excluded or _name_pattern(c.url) in bad_names:
            continue
        if skeletons and ax.url_skeleton(c.url) not in skeletons:
            continue
        picked.append(c)
    roles = {c.url: "content" for c in picked}
    ax.dedupe_content(picked, roles, {})
    picked = [c for c in picked if roles[c.url] == "content"]
    if rules.get("order_by") == "filename" and all(ax._file_number(c.url) is not None for c in picked):
        picked.sort(key=lambda c: ax._file_number(c.url))
    if not picked:
        return None, "the saved profile matched no images on this page"
    return picked, ""


def comic_data_from_roles(page, candidates, roles: dict, positions: dict = None,
                          method: str = "correction") -> dict:
    """The Review screen's comic corrections (include/exclude, cover/ad/
    content, reorder) as a result -- which images, in which order; the
    images themselves are never touched."""
    return ax.comic_data(page, candidates, roles, method=method, order=positions or {},
                         reasons={u: "marked by you" for u, r in roles.items() if r != "content"})
