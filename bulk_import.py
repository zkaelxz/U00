"""
bulk_import.py -- pulls a whole page's worth of catalog entries (title,
author, format) from a tag/category/ranking listing page in one go,
instead of importing one title at a time. Built for pages like JJWXC's
Baihe tag listing or Fanjiao's ranking page.

Same boundary as everywhere else in this app: this only ever extracts
bibliographic/cataloging metadata -- title, author's name, tags, and a
format indicator (does this title have an audio drama adaptation) --
never the actual chapters/episodes themselves.

Uses an LLM to extract entries from the page's visible text rather
than hardcoded CSS selectors, since exact page structure can't be
verified from the environment this was built in and is more likely to
survive the site's HTML changing. If a listing page is paginated,
pass each page's URL separately (see paginate_urls() below for
building a URL sequence from a pattern).
"""

import re
import json
from translate_engines import call_llm_json


def paginate_urls(base_url_pattern: str, start_page: int = 1, end_page: int = 5) -> list:
    """Builds a list of page URLs from a pattern containing {page}, e.g.
    paginate_urls('https://www.jjwxc.net/tag.php?tag=百合&page={page}', 1, 10)
    -> 10 URLs. You supply the pattern -- copy it from your browser's
    address bar after clicking to page 2 of the listing, to get the
    exact query parameter the site actually uses."""
    return [base_url_pattern.format(page=p) for p in range(start_page, end_page + 1)]


def extract_listing_entries_llm(page_text: str, engine, source_name: str = "",
                                 max_chars: int = 12000):
    """Asks the LLM to pull a list of catalog entries out of a tag/
    ranking listing page's visible text. Returns a list of dicts:
    {title, author, has_audio_drama, tags}. Only cataloging facts --
    instructed explicitly not to include any chapter/story content."""
    if not getattr(engine, "supports_reference", False):
        return []

    snippet = page_text[:max_chars]
    prompt = (
        "Below is the visible text scraped from a listing/ranking page on a Chinese web "
        f"fiction or audio drama platform{f' ({source_name})' if source_name else ''}. "
        "The page lists multiple titles (a tag category page, or a ranking list). "
        "Extract EVERY distinct title you can find as a separate entry. For each one, "
        "note: the title, the author (if shown), whether there's any indicator this title "
        "has an audio drama adaptation (look for mentions like 广播剧, 音频, a microphone "
        "icon/emoji, 'has audio' labels, or similar -- if genuinely unclear, say false "
        "rather than guessing), and any genre/pairing tags shown.\n\n"
        "Only extract cataloging metadata (title, author name, format/tag labels) -- do NOT "
        "include any synopsis, chapter content, or story text even if present on the page.\n\n"
        'Return ONLY a JSON array: [{"title": "...", "author": "...", "has_audio_drama": true/false, '
        '"tags": "comma, separated"}]. No preamble, no markdown fences.\n\n' + snippet
    )
    text = call_llm_json(engine, prompt, max_tokens=4000, fallback=None)
    if text is None:
        return []

    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        entries = json.loads(text)
    except json.JSONDecodeError:
        return []
    return entries if isinstance(entries, list) else []


def fetch_and_extract_listing(url: str, engine, source_name: str = "",
                                allow_render: bool = True):
    """One page: fetch + extract. Returns (entries, status) so a caller
    can distinguish 'this page genuinely had no titles' from 'this page
    is JavaScript-rendered and we only got the shell'. A failure here
    returns empty rather than raising, so one bad page doesn't stop a
    multi-page batch."""
    import page_fetch
    result = page_fetch.smart_fetch(url, allow_render=allow_render)
    if not result["text"].strip():
        return [], {"ok": False, "message": result["message"],
                    "needs_manual": result["needs_manual"]}
    entries = extract_listing_entries_llm(result["text"], engine, source_name=source_name)
    return entries, {"ok": bool(entries), "needs_manual": result["needs_manual"],
                     "message": (result["message"] if entries else
                                 "Read the page but found no titles in it. " + result["message"])}


def extract_listing_from_text(page_text: str, engine, source_name: str = ""):
    """Manual-paste path -- extracts from text you copied yourself."""
    return extract_listing_entries_llm(page_text, engine, source_name=source_name)


def bulk_extract(urls: list, engine, source_name: str = "", language: str = "zh",
                  progress_cb=None):
    """Runs fetch_and_extract_listing across multiple page URLs (for a
    paginated listing) and returns a deduplicated combined list of
    entries with a 'source_url' attached to each. Doesn't write to the
    database -- pair with a review step before committing, since LLM
    extraction from an arbitrary page layout can miss or misparse
    entries and you'll want to skim before bulk-inserting."""
    seen_titles = set()
    combined = []
    statuses = []
    for i, url in enumerate(urls):
        entries, status = fetch_and_extract_listing(url, engine, source_name=source_name)
        statuses.append({"url": url, **status})
        for e in entries:
            title = (e.get("title") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            e["source_url"] = url
            e["language"] = language
            combined.append(e)
        if progress_cb:
            progress_cb((i + 1) / len(urls))
    return combined, statuses


def commit_entries_to_library(db_module, entries: list, source_name: str = "manual"):
    """Inserts reviewed/approved entries into known_titles. `entries`
    should be the (possibly user-edited) list from bulk_extract(),
    each with title/author/has_audio_drama/tags/source_url/language."""
    added = 0
    for e in entries:
        media_type = "audio_drama" if e.get("has_audio_drama") else "novel"
        db_module.create_known_title(
            title_original=e.get("title", ""), title_en="",
            author=e.get("author", ""), tags=e.get("tags", ""),
            summary_en="", summary_original="",
            source_name=source_name, source_url=e.get("source_url", ""),
            language=e.get("language", "zh"), media_type=media_type,
        )
        added += 1
    return added
