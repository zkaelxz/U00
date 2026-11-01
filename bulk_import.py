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
survive the site's HTML changing. The Discover page builds the URL
list for a paginated listing and sends each page separately.
"""

from engine_backends.shared import load_json_reply
from engine_backends.llm_tasks import call_llm_json

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
    text = call_llm_json(engine, prompt, max_tokens=4000, fallback=None, expect="json_array")
    if text is None:
        return []

    entries = load_json_reply(text, [])
    return entries if isinstance(entries, list) else []


def extract_listing_from_text(page_text: str, engine, source_name: str = ""):
    """Manual-paste path -- extracts from text you copied yourself."""
    return extract_listing_entries_llm(page_text, engine, source_name=source_name)
