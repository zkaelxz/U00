"""
sources/ai_extract_media.py -- video/audio/subtitle resources on an
unrecognized page: candidates, the model's id-keyed roles and the checks.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from .ai_extract import HIGH, MEDIA_ROLES, MEDIUM, PageModel, _field, _presence, well_formed
from .ai_extract_comic import comic_candidates
from .generic_import import MIN_NOVEL_CHARS

_MEDIA_KIND = {"m3u8": "manifest", "mpd": "manifest", "mp4": "video", "webm": "video",
               "mkv": "video", "mov": "video", "m4v": "video", "m4a": "audio", "mp3": "audio",
               "aac": "audio", "ogg": "audio", "opus": "audio", "flac": "audio", "wav": "audio",
               "vtt": "subtitle", "srt": "subtitle", "ass": "subtitle", "ssa": "subtitle"}
_MEDIA_URL = re.compile(
    r"""(?:https?:)?(?:\\?/){2}[^\s"'<>()]+?\.(%s)(?:\?[^\s"'<>()]*)?""" % "|".join(_MEDIA_KIND),
    re.I)


@dataclass
class MediaCandidate:
    url: str
    kind: str                     # video / audio / manifest / subtitle / embed
    attr: str = ""
    language: str = ""
    label: str = ""
    hint: str = ""


def _kind_for(url: str, default: str = "") -> str:
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(url).path else ""
    return _MEDIA_KIND.get(ext, default)


def media_candidates(html: str, url: str) -> list:
    """Every media/subtitle resource the page itself exposes: <video>/
    <audio>/<source>/<track>, og:video metadata, embedded players, and
    media URLs sitting as text in inline scripts/JSON. Identify only --
    nothing is fetched, decoded or played."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    out, seen = [], set()

    def add(raw, kind, attr, language="", label="", hint=""):
        raw = (raw or "").strip().replace("\\/", "/")
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            return
        if raw.startswith("//"):
            raw = (urlsplit(url).scheme or "https") + ":" + raw
        absolute = urljoin(url, raw)
        if absolute in seen:
            return
        seen.add(absolute)
        out.append(MediaCandidate(absolute, kind or _kind_for(absolute, "video"), attr,
                                  language, label, hint))

    for tag in soup.find_all(["video", "audio"]):
        if tag.get("src"):
            add(tag["src"], _kind_for(tag["src"], tag.name), f"<{tag.name} src>")
        for s in tag.find_all("source"):
            add(s.get("src"), _kind_for(s.get("src") or "", tag.name), f"<{tag.name}><source>",
                label=s.get("label") or s.get("res") or s.get("size") or "",
                hint=s.get("type") or "")
        for t in tag.find_all("track"):
            add(t.get("src"), "subtitle", f"<track kind={t.get('kind') or 'subtitles'}>",
                language=t.get("srclang") or "", label=t.get("label") or "")
    for prop in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
        for m in soup.find_all("meta", attrs={"property": prop}) + \
                soup.find_all("meta", attrs={"name": prop}):
            add(m.get("content"), _kind_for(m.get("content") or "", "video"), f"<meta {prop}>")
    for f in soup.find_all("iframe", src=True):
        if re.search(r"player|embed|video", f["src"], re.I):
            add(f["src"], "embed", "<iframe>", hint="embedded player from another page")
    for script in soup.find_all("script"):
        body = script.string or script.get_text() or ""
        for m in _MEDIA_URL.finditer(body):
            add(m.group(0), _kind_for(m.group(0).replace("\\/", "/")), "script/JSON")
    return out


def resource_types(html: str, url: str) -> list:
    """Which resource types the page actually exposes to
    the session that read it (ContentAccess values), from the same
    deterministic detectors the extraction tiers use -- nothing fetched."""
    from .generic_import import extract_main_text_heuristic
    from .models import ContentAccess
    found = []
    if len(extract_main_text_heuristic(html)) >= MIN_NOVEL_CHARS:
        found.append(ContentAccess.TEXT.value)
    if len(comic_candidates(html, url)) >= 3:
        found.append(ContentAccess.IMAGES.value)
    kinds = {c.kind for c in media_candidates(html, url)}
    for kind_set, value in (({"video", "manifest", "embed"}, ContentAccess.VIDEO.value),
                            ({"audio"}, ContentAccess.AUDIO.value),
                            ({"subtitle"}, ContentAccess.SUBTITLES.value)):
        if kinds & kind_set:
            found.append(value)
    return found


def content_access_for(types) -> str:
    from .models import ContentAccess
    types = list(types or ())
    if not types:
        return ContentAccess.UNKNOWN.value
    return types[0] if len(types) == 1 else ContentAccess.MIXED.value


def media_payload(page: PageModel, candidates) -> str:
    lines = [f"URL: {page.url}", f"PAGE TITLE: {page.page_title}", "RESOURCES:"]
    for i, c in enumerate(candidates):
        d = {"id": f"m{i}", "url": c.url, "kind": c.kind, "found_in": c.attr}
        if c.language:
            d["srclang"] = c.language
        if c.label:
            d["label"] = c.label
        if c.hint:
            d["hint"] = c.hint
        lines.append(json.dumps(d, ensure_ascii=False))
    return "\n".join(lines)


MEDIA_PROMPT = (
    "Below are the media resources found on one web page (video, audio, streaming manifests, "
    "subtitle files, embedded players), in page order. Decide what each one is. Roles: "
    "\"main\" (the page's actual video/audio), \"alternate\" (the same content in another "
    "quality/format), \"subtitle\", \"audio_track\", \"trailer\", \"ad\", \"preview\", "
    "\"unrelated\".\n\nReturn ONLY a JSON object: {\"title\": the video's title exactly as "
    "written on the page or null, \"resources\": [{\"id\": \"m0\", \"role\": \"...\", "
    "\"language\": subtitle/audio language code or null, \"confidence\": 0-1}]}. Use only "
    "the ids given -- never invent URLs. null for anything you can't identify confidently. "
    "No preamble, no markdown fences.\n\n")


def media_data(page: PageModel, candidates, roles: dict, *, method: str, languages: dict = None,
               confidences: dict = None, title=None, protection=()) -> dict:
    languages, confidences = languages or {}, confidences or {}
    resources = [{"index": i, "resource_url": c.url, "kind": c.kind,
                  "role": roles.get(c.url, "unrelated"),
                  "language": languages.get(c.url) or c.language or None, "label": c.label or None,
                  "found_in": c.attr, "confidence": confidences.get(c.url)}
                 for i, c in enumerate(candidates)]
    return {"content_type": "video", "url": page.url, "method": method, "title": title,
            "resources": resources, "protection": list(protection), "rejected": {}}


def deterministic_media(page: PageModel, candidates, protection=()) -> tuple:
    """(data, ambiguous_reasons). Unambiguous = exactly one playable
    main-looking resource; subtitles keep their declared language."""
    main_like = [c for c in candidates if c.kind in ("video", "manifest", "audio")]
    roles = {}
    for c in candidates:
        if c.kind == "subtitle":
            roles[c.url] = "subtitle"
    why = []
    if len(main_like) == 1:
        roles[main_like[0].url] = "main"
    elif not main_like:
        why.append("no playable video/audio resource found on the page")
    else:
        why.append(f"{len(main_like)} possible video/audio resources -- which one is the "
                   "actual content isn't clear")
    for c in candidates:
        roles.setdefault(c.url, "unrelated" if c.kind != "embed" else "alternate")
    title = next((b.text for b in page.blocks if b.tag in ("h1", "h2")), None) or page.page_title or None
    return media_data(page, candidates, roles, method="deterministic", title=title,
                      protection=protection), why


def media_from_picks(page: PageModel, candidates, picks: dict, protection=()) -> dict:
    picks = picks if isinstance(picks, dict) else {}
    by_id = {f"m{i}": c for i, c in enumerate(candidates)}
    roles, langs, conf = {}, {}, {}
    for item in picks.get("resources") or []:
        if not isinstance(item, dict) or item.get("id") not in by_id:
            continue
        c = by_id[item["id"]]
        roles[c.url] = item.get("role") if item.get("role") in MEDIA_ROLES else "unrelated"
        if isinstance(item.get("language"), str) and item["language"].strip():
            langs[c.url] = item["language"].strip()
        if isinstance(item.get("confidence"), (int, float)):
            conf[c.url] = float(item["confidence"])
    # A subtitle file is a subtitle whatever the model called it.
    for c in candidates:
        if c.kind == "subtitle":
            roles[c.url] = "subtitle"
    t = picks.get("title")
    return media_data(page, candidates, roles, method="llm", languages=langs, confidences=conf,
                      title=str(t).strip() if isinstance(t, str) and t.strip() else None,
                      protection=protection)


def validate_media(data: dict, page: PageModel) -> dict:
    conf = {}
    res = data["resources"]
    checks, score = [], 0.9
    for r in res:
        if not well_formed(r["resource_url"]):
            r["role"] = "unrelated"
            checks.append(f"dropped a malformed URL ({r['resource_url'][:60]})")
    main = [r for r in res if r["role"] == "main"]
    if not main:
        score = 0.0
        checks.append("no main video/audio resource identified")
    elif len(main) > 1:
        score = 0.6
        checks.append(f"{len(main)} resources marked as the main one")
    if data.get("protection"):
        checks.append("DRM/protection markers on this page -- the app won't decrypt or work "
                      "around anything; a protected stream will fail to download")
    conf["media_resources"] = _field(score, checks)
    c = []
    conf["title"] = _field(_presence(page, data.get("title"), c), c)
    if conf["title"]["score"] < 0.5 and data.get("title") is not None:
        data["rejected"]["title"] = data["title"]
        data["title"] = None
    data["confidence"] = conf
    data["overall"] = {"score": conf["media_resources"]["score"],
                       "bucket": conf["media_resources"]["bucket"]}
    data["valid"] = conf["media_resources"]["bucket"] in (HIGH, MEDIUM)
    data["problems"] = list(conf["media_resources"]["checks"])
    return data
