"""
services/extension_novel_service.py -- the extension bridge's "Save text into
a title": a web-novel page's text (or the person's selection) appended to a
novel title's raw-novel file as a new chapter, the same append the Saved raw
chapters panel lists (`sources.pipeline.save_novel_text`).

The answer goes back through `page_server.py` to a script on a third-party
page, so it carries booleans and ids only: never the text, the page URL or a
filesystem path, and every message is a fixed sentence run through
`translate_engines.redact_secrets`.
"""

import re

import background_jobs
import db
import page_server
from lib.errors import (ConflictError, ForbiddenError, InvalidInputError, NotFoundError,
                        ServiceError)
from services import drama_service, ownership_service
from sources import pipeline
from translate_engines import redact_secrets

# A long web-novel chapter page is tens of thousands of characters; this
# leaves room for the longest while refusing a whole book or a runaway page
# scrape, which would land in the raw-novel file as one "chapter" the Saved
# raw chapters panel and the translation context then re-read on every use.
# extension/content.js refuses above the same number before sending.
MAX_CAPTURE_CHARS = 200_000
MAX_HEADING_CHARS = 200     # the chapter manifest keeps this much of a title
MAX_SOURCE_CHARS = 60       # and this much of a site name

# The bridge holds no signed-in user: it answers only loopback requests that
# carry the PC's own token, which is the local owner's access, as for every
# local_only() route.
_BRIDGE_PRINCIPAL = {"user_id": None, "is_local_owner": True}

# Lone surrogates (a client that cut a string mid-character) cannot be
# encoded as UTF-8 and would fail every save of the chapter.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff‎‏‪-‮⁦-⁩]")
_LINE_BREAKS = re.compile(r"\r\n?|[  \x85]")



class _TooLong(InvalidInputError):
    pass


# Most specific first: _TooLong is also an InvalidInputError.
_STATUS = ((_TooLong, 413), (InvalidInputError, 422), (NotFoundError, 404),
           (ForbiddenError, 403), (ConflictError, 409))
_FAILED = "Could not save the text into that title; try again."


def clean_text(text: str) -> str:
    """Keeps line breaks and tabs; drops the other control and bidi-override
    characters a page can carry, which would otherwise sit invisibly in
    the raw text and the prompts built from it."""
    return _CONTROL.sub("", _LINE_BREAKS.sub("\n", text)).strip()


def _one_line(value, cap: int) -> str:
    return re.sub(r"\s+", " ", clean_text(str(value or "")))[:cap].strip()


def save_page_text(drama_id, heading: str, text: str, source: str = "", url: str = "",
                   principal=_BRIDGE_PRINCIPAL) -> dict:
    """Appends `text` to the novel title `drama_id` as a new chapter titled
    `heading`. A repeat capture of the same page (same title and text) is
    a no-op that says so. Sets the title's source link once, if it has
    none."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("Pick a title to save into.")
    if not isinstance(text, str):
        raise InvalidInputError("No text was sent.")
    if len(text) > MAX_CAPTURE_CHARS:
        raise _TooLong(f"The text is longer than {MAX_CAPTURE_CHARS:,} characters; "
                                "select one chapter's worth and save that.")
    body = clean_text(text)
    if not body:
        raise InvalidInputError("No text was found to save.")
    ownership_service.require_editable(principal, "drama", drama_id)
    drama = db.get_drama(drama_id) or {}
    if (drama.get("media_type") or "").lower() != "novel":
        raise InvalidInputError("Text saves into a novel title. Pick one, or create one first.")
    if background_jobs.is_running(pipeline.import_job_id(drama_id)):
        raise ConflictError("A chapter import is running for this title; "
                            "save the page once it finishes.")
    heading = _one_line(heading, MAX_HEADING_CHARS)
    url = url if isinstance(url, str) else ""
    added = pipeline.append_novel_chapter_once(
        drama_id, body, heading, url=url, source=_one_line(source, MAX_SOURCE_CHARS))
    link_set = drama_service.set_source_url_once(drama_id, url) if added and url else False
    return {"saved": added, "already_saved": not added, "drama_id": drama_id,
            "chars": len(body), "source_link_set": bool(link_set),
            "message": ("Saved as a new chapter." if added else
                        "This chapter is already saved in that title; nothing was added.")}


def save_from_bridge(payload: dict) -> dict:
    """page_server's POST /novel: `save_page_text` with its errors turned
    into the bridge's own, since a raw exception could carry a path."""
    try:
        return save_page_text(payload.get("drama_id"), payload.get("heading"),
                              payload.get("text"), payload.get("source"), payload.get("url"))
    except ServiceError as e:
        status = next((code for cls, code in _STATUS if isinstance(e, cls)), 500)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None
    except Exception:
        import applog
        applog.get_logger().warning("Saving extension text into a title failed", exc_info=True)
        raise page_server.EndpointError(500, _FAILED) from None
