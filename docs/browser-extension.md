# Translate the page you're reading

A browser extension that sends what you're looking at into Baihe: either a comic page, with the translation drawn over it in place, or a block of page text, translated in a panel on the page.

This does **not** replace the source adapters in `sources/` (bulk import, chapter tracking, an offline library). This is "translate what I'm looking at right now."

## Why it's worth having

It reaches pages the adapters can't (blob-protected chapters like manhuaku's, tile-scrambled readers, sessions that need a sign-in, and sites with no adapter) **without this app touching a protection mechanism**. Your own browser has already decrypted, descrambled and authenticated the page because you're reading it legitimately, and the extension reads what is already on your screen. It also reads text-heavy pages such as a web novel chapter.

## Setting it up

1. In Baihe: **Settings → Browser extension** → turn on the **Extension
   bridge** switch. It's off by default because it opens a port
   (`127.0.0.1:8756`). Turning it off again closes that port straight away.
2. Pick the engine extension pages should be translated with. It uses
   that engine's configured key. With no key set, pages
   still come back with their original text read by OCR, clearly marked
   as untranslated.
3. Copy the **token** shown there.
4. In Chrome or Edge: `chrome://extensions` → **Developer mode** → **Load
   unpacked** → choose this project's `extension/` folder.
5. Open the extension's **options** and paste the token. There is no port
   setting: Baihe always serves the bridge on 8756, and the extension's only
   host permission is `http://127.0.0.1:8756/*`. It'll say *Connected* and
   how many dramas it can see. If the bridge switch is off the extension
   says it couldn't reach Baihe and names the switch to turn on.

## Using it

Click the extension on a page you're reading:

- **Translate this page** — the largest page-sized image on screen.
- **Everything visible** — a spread, or a whole visible strip. For a whole chapter in a scrolling reader, see *Capturing a whole chapter* below.
- **Send pages to** — which drama they land in. Remembered per site, so
  reading a long series isn't a per-page decision.
- **Also save the page into that drama** — untick to translate for
  reading only, without importing anything. The result line says where
  the pages went: "Sent N pages to *title*", or, with saving off, that
  they were drawn on the page only and not saved. Pages already
  translated in this tab show as "M already translated, not sent
  again". After a save, **Open in Baihe** opens that drama's comic page
  (`http://127.0.0.1:8600/#/comic/<id>`, the app's default port; the link
  holds only the drama id). The picker repeats the full title of the
  chosen drama below it, since a dropdown truncates long titles. It
  doesn't show saved page counts: the `/health` list the popup reads has
  none.
- **Draw over page** / **Show / hide** — the
  overlay toggle. Click any overlaid bubble to see the original text
  underneath it.

### Capturing a whole chapter

Some readers (vertical-scroll ones with a "5 / 70" counter) mount only the pages near the screen, so **Everything visible** sees a handful. Use:

- **Capture whole chapter** — scrolls to the top, then down the reader about 0.9 of a screen per step, pausing 250–600 ms between steps like a person reading.
- **Capture from here** — the same, starting from where you are.

After each step it waits for the reader to mount and load new pages, reads those that are new, and sends them in order, 12 at a time, drawing the translation as each batch comes back. Progress reads "Page 24 of 70" when the reader shows its own counter, otherwise a count. The page also shows a small box with **Cancel**, because the popup closes the moment you click away (the capture carries on without it); the popup's **Cancel capture** does the same. Your scroll position is put back afterwards.

It stops, and says why, when it reaches the end of the chapter, finds no new pages for 6 steps in a row, hits the 300-page limit, is cancelled, or a send fails. Pages already translated stay saved and drawn.

How it behaves:

- **Order.** Pages are ordered by the reader's own index when its elements carry one (`data-index`, `data-page`, `aria-posinset`), otherwise by position in the scrolled content, and sent in that order, so the saved pages land in the drama in chapter order.
- **Cost.** A capture can send up to 300 pages, each a separate engine call. The bridge's `/health` only says whether an engine is configured, not whether it is paid, so the popup can't warn about it; check your engine's pricing before capturing a long chapter with a paid one. While a capture runs, the popup's Translate buttons are disabled and the page refuses a plain translate, so no page is saved twice.
- **Duplicates.** Pages are de-duplicated by content hash, so two identical pages in a chapter (say, blank ones) count once. Running it again on the same tab skips everything already translated; after a reload the cache is empty, so a second capture into the same drama saves the pages again.
- **The site is left alone.** It makes no requests of its own and never touches the site's APIs or tokens: it only reads pixels the reader's own JavaScript has already drawn for you, one step at a time. It needs no extra permissions.
- **Overlays.** A virtualised reader throws pages away when they scroll far off, and their overlays go with them. The translation is already sent and saved; scrolling back does not redraw it.

Paging back to something already translated is instant: results are
cached by image content hash, so nothing is ever translated twice.

### Translating a page's text

A separate section of the popup, for prose rather than comic pages:

- Pick a direction (**zh/ja/ko → English**, or **English → zh/ja/ko**) —
  the same directions the **Translate** page in Baihe itself supports,
  since this reuses that exact pipeline.
- **Translate text** — if you've selected text on the page,
  that selection is what gets sent. With nothing selected, the extension
  captures the page's own largest contiguous block of paragraph text
  (skipping `<nav>`/`<header>`/`<footer>`/`<aside>` and anything too
  short to be real prose), a simple heuristic rather than a full
  readability implementation.
- The translation appears in a small panel drawn onto the page itself —
  not the popup, which is too narrow for a page-length passage and closes
  the moment it loses focus, exactly when you want to keep reading. The
  panel has a **Show original** toggle and a close button, and it's
  reused on the next translate rather than stacking a second one.
- Every translation is also saved into the **Translate** page's history,
  the same table that page writes to.

The extension has no engine picker or key of its own; it uses the engine
set in Settings → Browser extension. Page OCR follows the same saved settings
as Comic Scanlate (Tesseract path, OCR backend, the PaddleOCR-VL preference
and the Hugging Face token), read by the app on every request; none of them
reach the extension.

## Two things it checks for before capturing

- **A page that's still descrambling/reassembling isn't captured mid-way.**
  Before reading an element's pixels, the extension takes a cheap sample,
  waits, and takes another; it only proceeds once two samples in a row
  match (bounded to 1.5s, then it proceeds anyway rather than hang
  forever). This matters for a tile-scrambling reader like mangaz.com's, whose JS
  reassembles a tile-scrambled page onto a canvas after it loads --
  capturing the instant you click, rather than once that's settled, could
  grab a half-drawn frame. An already-static image settles in well under
  a fifth of a second, so this adds no noticeable delay to the ordinary
  case.
- **A verification/CAPTCHA interstitial is recognized and refused, not
  mistranslated.** If the page you click on looks like a Cloudflare/bot
  challenge (by title, visible text, or a known CAPTCHA widget actually
  on screen — not just present somewhere in the DOM), the extension says
  so plainly instead of silently sending whatever image-sized element
  happens to be on that page off for OCR. It never tries to solve or pass
  the challenge, the same posture `sources/http.py`'s `ChallengeDetected`
  already takes server-side — recognize and hand off, never fight it.

## What it can't do

- **A cross-origin image the site draws without CORS can't be read.**
  The browser refuses to let any script read those pixels, and the
  extension respects that rather than working around it. You'll get a
  plain message saying so.
- **Chrome won't let an extension run on some pages at all** — the
  extensions gallery, the built-in PDF viewer, `chrome://` pages.
- **Canvas-only viewers** give no `<img>` to anchor an overlay to, so
  positioning falls back to the canvas element's own box.
  During a chapter capture, a canvas is re-hashed before bubbles are drawn, and skipped if
  the reader repainted it with another page meanwhile.
- **Two-page spreads and right-to-left order** affect which box belongs
  to which page; the app derives reading order per page, but a spread
  sent as one image is treated as one page.
- **Webtoon strips** are long single images. The app slices them
  server-side, so a very tall strip is best sent with the reader scrolled
  to the part you care about.
- **Firefox is not supported yet.** MV3 covers Chrome and Edge; Firefox
  differs enough to be its own work.
- **The free bubble detector is not usable on colour artwork.** On a real colour 4-koma page it found no bubbles where the ML backend found 63 regions. If pages come back with nothing overlaid, that's usually this. The extension uses Auto, which picks the ML model once its weights are downloaded: on a comic, open **Translate pages → Advanced → Text detector → ML model** and run one page to fetch them.
- **For Japanese, use `manga_ocr`, not Tesseract.** Manga is vertical text; Tesseract returned unreadable fragments on the same page. Auto already picks `manga_ocr` for Japanese.
- **The "largest paragraph block" heuristic can pick the wrong
  block on an unusual layout** — a page with no real `<p>` tags (some
  sites lay out prose in bare `<div>`s), or one where a comment section
  happens to out-weigh the actual chapter. Select the passage yourself
  when that happens; an explicit selection always wins.
- **`/text` only translates one side of a pair with English**,
  the same limit the Translate page already has — a
  non-English-to-non-English page (say, a Japanese site's Chinese fan
  translation) isn't a supported direction.

## How it's put together

```
extension/                     the browser side
  manifest.json                MV3; loopback host permission only
  background.js                the service worker: holds the token, makes the calls
  content.js                   injected on a click: collects images/text, draws overlays/panel
  popup.html / popup.js        pick a drama, send, toggle
  options.html / options.js    paste the token
page_server.py                 the endpoint, on a thread started by the API
services/extension_service.py  the opt-in switch, the token, the engine setting
api/routers/extension_routes.py  /api/extension/*, PC-only; UI in Settings → Browser extension
```

The endpoint's four routes:

| Route | What it does |
|---|---|
| `GET /health` | Confirms the app is up, and lists the dramas to send to. |
| `POST /page` | One image. |
| `POST /pages` | Several — a spread, or everything visible. |
| `POST /text` | A block of raw page text. |

Everything funnels into the existing, tested pipeline
(`scanlate.detect_and_ocr_page` → `scanlate.translate_page_bubbles`) and
the same `sources.pipeline.add_page_images` import path Scanlate's own
manual upload uses. There is deliberately no second translation path, and
which images count as real pages is decided by
`sources/generic_import.py`'s existing filter rather than a second
implementation in JavaScript that would drift from it.

`/text` follows the same "one pipeline" rule from the other side: it
funnels into `translate_engines.standalone_translate`, the exact function
`services/translate_service.py` calls for the Translate page, rather
than a second translation path for text captured by the extension. It
does no detection or OCR -- the extension already sends real text, not
pixels -- so it's a much thinner route than `/page`/`/pages`: validate
the input, translate it, save it to history, return it.

Stdlib `http.server`, so no new dependency and nothing to register in
`diagnostics.py`'s `OPTIONAL_DEPENDENCIES`.

## Security

The endpoint accepts images and runs OCR, and **any page in any browser
tab can make requests to localhost**. So:

- It binds `127.0.0.1` only, never `0.0.0.0`, and re-checks the peer
  address on every request — so a later change to the bind address can't
  silently open it up.
- Every route, `/health` included, requires the token in the
  `X-Baihe-Token` header, compared with `secrets.compare_digest`. Never
  in a URL, a log line or a stored error, per this repo's existing secret
  rule (`translate_engines.redact_secrets`).
- **Requiring a custom header is itself the cross-site defence.** A web
  page can't set one on a cross-origin request without a successful CORS
  preflight, so the endpoint answers no preflight and sends no
  `Access-Control-Allow-Origin` at all. That's why the extension fetches
  from its *service worker*, which isn't subject to page CORS. If you
  ever hit a CORS error here, move the request into the worker — **never**
  add a permissive header to the server.
- The token lives only in the service worker. The content script, which
  shares a page's world, never sees it. That's still true of the text
  path: `content.js` never imports or reads the token, and gathering the
  page's text and sending it happen in two different worlds, the same
  split as the image path.
- Body size, per-image size and image count are capped, only real image
  content types are accepted, and every connection has a timeout.
- `/text` carries the same posture, sized to what it actually accepts:
  captured text is capped at `MAX_TEXT_CHARS` (20,000 characters, a
  generous chapter), the source/target language pair is validated against
  a fixed set rather than passed through freely, and it shares `/page`'s
  body-size cap, peer check, token check, and lack of a CORS preflight.
- API keys are never written to disk by any of this. The engine choice is
  an app setting (`extension_translation_engine`); the endpoint resolves it
  and reads the key from `.env` on each request (`services/extension_service.py`).

`tests/test_extension_manifest.py` pins the browser-side half of that
statically, because none of it can be checked by running the app.

## Remote mode (planned; tokens built)

Today the extension talks only to the bridge on this PC (127.0.0.1:8756,
one shared token). Remote mode will let a household member's own computer
(desktop Chrome or Edge) reach Baihe through the household address, and is
being built in three parts:

1. **Per-device tokens (built).** Settings > Browser extension devices: a
   signed-in member with the `extension.send` permission names a device
   and gets a token once (`baihe_dt_…`, 256 random bits; Baihe stores only
   its SHA-256). They see when each was last used (time and a coarse
   network prefix) and revoke one at once; at most 10 active per person,
   5 new per hour, expiry (30 days, 90 when none is given, a year or
   never, which must be chosen explicitly). The owner at the PC (or an admin there) sees and revokes
   everyone's. Losing `extension.send` or the account revokes a person's
   tokens. `api.auth.require_device_token()` checks a token: only from
   `Authorization: Bearer`, never a cookie or URL; one 401 for any bad
   token, failures throttled per address; the person must still hold
   `extension.send`; the token acts with member rights only, even for an
   admin account. No route accepts a device token yet, and no existing
   route ever does (`tests/test_device_tokens.py`).
2. **Bridge routes in the API (next).** The `/page` and `/text` work moves
   behind `require_device_token()` with the same size and count caps, the
   caller's drama ownership, the spending cap and no fetching of URLs.
3. **Extension (after).** A configurable Baihe address and token in the
   options, https only for anything but loopback, host permission asked
   for at run time, and the loopback mode kept working.

The PC-only shared token above is unchanged and still what the extension
on the PC itself uses. Port 8756 is never routed by the reverse proxy.

## What was actually verified

Mocked tests: `tests/test_page_server.py`, including every refusal above.

The extension was also run for real, repeatably:

```
python extension/verify_end_to_end.py
```

It loads the extension unpacked into a real Chromium, points it at a real running endpoint, and drives a page holding a normal `<img>` and a `blob:`-backed one. It needs Playwright with a **full** Chromium build (the headless shell can't load extensions), exits 77 (skipped, not passed) without one or when port 8756 is in use, touches no real site and needs no API key. Run it after changing anything in `extension/` or `page_server.py`. It establishes:

- A `blob:`-backed page image is translated end to end and lands in the library.
- Overlay boxes scale correctly and stay exact after a resize; click-to-see-original and the overlay toggle work.
- The same image twice on a page is sent once, and re-translating a translated page comes from the cache.
- With nothing selected, the largest paragraph block is captured and a `<nav>` is not; an explicit selection overrides it; the text runs through the real `standalone_translate` and is saved to history.
- A page titled `"Just a moment..."` is refused with `CHALLENGE_DETECTED`.

### Against a real mangaz.com chapter

A real Chromium and mangaz.com's own reader, one page load. The captured page was 1190x1684 (a descrambled page; a scrambled strip would be ~4760x421), read from the reader's `blob:` and saved as a 4.3MB PNG. On that page the ML detector found 63 regions and `manga_ocr` returned correct Japanese dialogue, while the free OpenCV detector found none and Tesseract produced garbage. Translation itself was not run (no API key there).

**Also not verified:** manhuaku.net and Bilibili Manga, and the real toolbar-click flow. Clicking the icon grants `activeTab`, which Playwright can't do, so that grant was simulated with a throwaway copy of the extension; the shipped manifest stays loopback-only.

Chapter capture was checked with a throwaway Playwright page that mounts and unmounts 70 images as it scrolls, like a virtualised reader (the extension's messaging stubbed): chapter order kept even with a shuffled DOM, a repeated page sent once, batches of at most 12, only a window of pages in the DOM at once, cancel, the stall stop, the 300 cap on 320 pages, and a second run sending nothing. A real Bilibili Manga chapter in real Chrome has not been tried.

The browser-side test suite is static only. There is no automated test
that drives a real browser, on purpose: this project's tests are mocked
throughout and CI has no browser.

## Marking a source as extension-only

If a site only works through the extension (its automated Static and Browser tests fail), open **Sources > Source settings > Details** and switch on **Works only with the browser extension**. It is your own note: the tests keep their real results, and the source shows *Extension only* instead of *Untested*. Pasted links, search, series and chapter imports, tracking and scheduled checks then stop before reading that site and point you here. If a later Static or Browser test passes, Details offers to clear the marker; it is never cleared automatically.

