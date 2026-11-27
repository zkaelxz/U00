"""services/extension_subs_service.py -- the English lines the browser extension draws over a page's own video or audio player, from the caller's title made from that very page."""

import core
import db
from services import drama_service, export_service, media_upload_service, page_server
from services.extension_novel_service import _BRIDGE_PRINCIPAL

# Several hours of dialogue; the answer stays well under a megabyte.
MAX_CUES = 5000


def _page_url(payload: dict) -> str:
    url = payload.get("page_url")
    if not isinstance(url, str) or not url.strip():
        raise page_server.EndpointError(400, "page_url is required")
    if len(url) > drama_service.MAX_URL_LEN:
        raise page_server.EndpointError(400, "page_url is too long")
    return url.strip()


def _whole_title(url: str, principal):
    for row in drama_service.titles_with_page_link(url, principal):
        own_link = (row.get("source_url_full") or row.get("source_url") or "").strip()
        if media_upload_service.downloaded_whole_from(row["id"], own_link):
            return row
    return None


def _cues(drama_id: int) -> tuple:
    names = export_service._speaker_display_names(drama_id)
    cues = []
    for ln in sorted(db.load_line_objects(drama_id), key=lambda ln: (ln.start, ln.end)):
        text = (ln.en or "").strip()
        if not text or ln.end <= ln.start:
            continue
        if ln.sfx:
            text = core.sfx_cue_text(text, italic_tags=False)
        cues.append({"start": round(float(ln.start), 3), "end": round(float(ln.end), 3), "en": text,
                     "speaker": names.get(ln.speaker or "", "")})
    return cues[:MAX_CUES], len(cues) > MAX_CUES


def subs_for_page(payload: dict, principal=_BRIDGE_PRINCIPAL) -> dict:
    """{"found": False}, or {"found": True, "title", "cues": [{start, end, en, speaker}], "cut_off"};
    cues is empty for a title with no English yet.
    Only a title whose media is the whole download of the page counts: a part starts at 0:00, so
    its lines would play at the wrong moments. Nothing about the link or the files goes back."""
    row = _whole_title(_page_url(payload), principal)
    if row is None:
        return {"found": False}
    cues, cut_off = _cues(row["id"])
    return {"found": True, "title": row.get("title_en") or row.get("title_zh") or "",
            "cues": cues, "cut_off": cut_off}
