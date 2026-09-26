# Adding a source adapter

An adapter teaches the app one site. Everything else is handled for you:
pacing, retries, challenge hand-off, health tracking, caching, the Sources
tab UI, multi-source search, chapter tracking and import into Scanlate or
Workspace. You never touch the translation engine, `db.py`, or any tab.

## 1. Check the site first

Do this before you write any code, and record what you find in
`docs/content-sources.md`:

- **robots.txt**: note the rules for `User-agent: *` and any
  `Crawl-delay`. If there is a crawl delay, it becomes your
  `host_min_interval`.
- **Terms**: record what the terms actually say, quoting where you can,
  in the capabilities `terms` block. Only a specific, written
  anti-scraping or AI-use clause makes a source `TOS_PROHIBITED`.
  Generic boilerplate doesn't.
- **Technical posture**: work down the ladder in the order below, and
  stop at the first tier that works.
  1. Static HTTP. Is it a real page or an empty JavaScript shell?
  2. Embedded JSON.
  3. Rendered browser.
  4. Authenticated browser.
  5. Official API.

  An active challenge (Cloudflare "Just a moment...", a CAPTCHA) is a
  wall. The app never tries to pass it automatically.
- **Protections**: DRM, site-side decryption, or signed/expiring tokens
  are recorded, not worked around.

## 2. Write the adapter

Create `sources/adapters/<site>.py`:

```python
import re
from ..base import SourceAdapter
from ..models import ChapterInfo, ContentType, PageRef, SearchResult, SeriesInfo
from ..registry import register

@register
class ExampleSource(SourceAdapter):
    name = "example"                       # stable id: health, tracking, settings key off this
    display_name = "Example (漫画站)"
    content_types = [ContentType.MANHUA.value]   # or NOVEL for a text source
    languages = ["zh"]
    url_patterns = [r"example\.com/comic/"]      # for the "Paste any URL" box
    host_min_interval = {"www.example.com": 10.0}  # e.g. robots.txt Crawl-delay
    default_headers = {"Referer": "https://www.example.com/"}
    MIRRORS = ["https://www.example.com", "https://m.example.com"]

    def search(self, query, page=1):
        resp = self.client.get_with_mirrors(f"/search?q={query}", self.MIRRORS, use_cache=False)
        ...
        return [SearchResult(self.name, series_id, title, url, cover)]

    def get_series(self, series_id): ...        # -> SeriesInfo
    def get_chapters(self, series_id): ...      # -> [ChapterInfo]
    def get_pages(self, chapter): ...           # -> [PageRef]      (image sources)
    def download_page(self, page):              # -> (bytes, ".jpg") (image sources)
        return self.client.get(page.url, classify_body=False,
                               headers=page.headers).content, ".jpg"
    # text sources implement get_chapter_text(chapter) -> str instead of pages
```

Then add the module name to `BUILTIN` in `sources/adapters/__init__.py`.

Rules:

- **Every request goes through `self.client`**, using `get`, `post` or
  `get_with_mirrors`. Never call `requests` directly. The client is what
  provides pacing, timeouts, retries, challenge detection and health.
- **Pass `use_cache=False` for anything that changes**, such as search
  results and chapter lists. Page images can use the cache.
- **Implement only what the site offers.** Unimplemented methods raise
  `NotSupportedError`, and the UI hides what an adapter doesn't support
  (`adapter.supports("get_pages")`).
- **Don't decode anything that's protection.** Decoding a compression or
  packing format that the page's own script unpacks for every visitor is
  fine. manhuagui's LZString is an example. Decrypting content, bypassing
  a token scheme, or solving a challenge is not. If content only appears
  after the site's own JavaScript runs, that's the `RENDERED_BROWSER`
  tier's job, not the adapter's.
- **Adult-content flags stay off by default.** Only send one if a
  setting explicitly opts in.
- **Parse CJK chapter titles as they are.** `chapter_order.sort_chapters`
  handles ordering, and the UI already calls it.

## 3. Test it offline

Tests never touch the network. Record a real response once, trim it, and
save it as a fixture. Then drive the adapter with a scripted transport
from `tests/sources_helpers.py`:

```python
from tests.sources_helpers import ScriptedTransport, html, make_client
client = make_client("example", ScriptedTransport({url: html(fixture)}))
adapter = ExampleSource(client=client)
```

Cover these cases:
- search parsing
- chapter-list parsing
- page-list parsing
- mirror fallback (primary raises or returns 5xx, then the backup
  answers)
- a clean error when the site's markup changes

## 4. What you get for free

- A row in **Sources → Sources, health & diagnostics**, with a
  🟢/🟡/🔴 light, a capabilities record, per-tier **Test Static**,
  **Test Browser** and **Test Authenticated** buttons, and diagnostics.
- A place in multi-source search and in the URL front door.
- Chapter tracking, with new-chapter notifications.
- Multi-chapter selective import. Pages land in Scanlate the same way a
  manual upload does, and text lands in Workspace's raw-novel file.
