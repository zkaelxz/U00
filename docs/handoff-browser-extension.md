# Handoff: browser extension for translating pages you're looking at

Written from the implementing side (2026-09-27) for the planning session
to turn into roadmap steps, the same way
`handoff-voice-bank-translate-zh-narration.md` was. Two steps, sequenced:
**27** gives the app a local endpoint and an extension that sends it the
page you're on; **27b** adds translations drawn over the page in place.
27b needs 27's endpoint, so they can't swap order, but 27 is useful
alone.

## Why this is worth doing, beyond convenience

It isn't only a nicer front door. It's the one approach that reaches
content the adapters structurally can't, and it does so **without this
app ever touching a protection mechanism** — the same line
`manhuaku.py` and `mangaz.py` already draw:

- **mangaz.com** serves tile-scrambled pages (a 1190x1684 page arrives as
  a ~4760x421 strip). Only its own reader reassembles them. Step 23l now
  drives that reader in a headless browser, which works but is slow
  (~3 minutes for a 43-page book), flaky in sandboxes
  (`net::ERR_TOO_MANY_RETRIES`), and has to be paced carefully to avoid
  being throttled.
- **manhuaku.net** delivers its own protected chapters as `blob:` URLs
  that only exist inside the rendering tab.
- **Bilibili Manga** needs a signed-in session for anything real.

In every one of those, **the person's own browser has already done the
work** — decrypted, descrambled, authenticated — because they're reading
the page legitimately. An extension reads what's already on their screen.
No headless driving, no pacing games, no session to forge, nothing to
circumvent. It's strictly less invasive than what this app does today.

It also covers sites with no adapter at all, which is most of them.

**It does not replace the adapters.** Those do bulk import, chapter
tracking, new-chapter checks and an offline library. This is "translate
what I'm looking at right now." Both should exist.

## What already exists (so neither step rebuilds it)

The whole translation pipeline is built and tested — `scanlate.py`:
`detect_bubbles()` -> OCR (`auto_ocr_backend()`) ->
`translate_page_with_context()` -> `render_text_in_box()` /
`process_page()`, plus `detect_panels()`, `classify_text_regions()`,
`sample_text_style()`, and webtoon handling (`is_webtoon_strip()`,
`split_webtoon_strip()`). Glossary, character names and per-drama style
already flow into `translate_page_with_context()`.

Neither step should add a second translation path. Both are new **input**
and **output** surfaces around the existing one.

---

## Step 27 — local endpoint + "send this page to Baihe"

### The endpoint

A small HTTP server bound to `127.0.0.1` only, started on a thread beside
Streamlit. **The planning session's own note is right**: this does not
need the deferred M8+ FastAPI/React migration. Python's own
`http.server` is enough; no new dependency, so nothing to register in
`diagnostics.py`'s `OPTIONAL_DEPENDENCIES` unless that changes.

- `GET /health` — for the extension to confirm the app is running.
- `POST /page` — image bytes plus `{source_url, series_title, page_no}`;
  runs the existing Scanlate pipeline; returns bubble boxes with original
  and translated text.
- `POST /pages` — the same for several images at once (a spread, or a
  whole visible strip).

### Security, per this repo's existing rules

- Bind `127.0.0.1`, never `0.0.0.0`. A local port that accepts images and
  runs OCR must not be reachable from the network.
- A token generated once into the library dir, shown in Settings to paste
  into the extension, required on every request. Any page in any tab can
  make requests to localhost, so an unauthenticated endpoint means any
  site could quietly drive this.
- The token follows the existing rule for secrets: header only, **never**
  in a URL, log line or stored error (`translate_engines.redact_secrets`
  is the precedent).
- Every request gets a timeout, per the standing rule (and
  `tests/test_static_analysis.py` enforces it for the modules it covers —
  worth extending it to this one).
- Reject non-image payloads and cap size, so a stray request can't pin
  the OCR backend.

### The extension (MV3)

- **Content script**: collects candidate page images from the DOM,
  including `blob:`-backed and canvas-rendered ones (`canvas.toBlob()`,
  or drawing an `<img>` at `naturalWidth`/`naturalHeight` to preserve
  full resolution — important, OCR quality depends on it). The
  "which images are real pages" problem is already solved server-side in
  `sources/generic_import.py` (`image_candidates()` +
  `filter_page_images()`: size, aspect clustering, cross-chapter repeats)
  — prefer sending candidates and reusing that, rather than writing a
  second filter in JavaScript that will drift from it.
- **Popup**: pick the target drama (or create one keyed by source URL /
  series), then "Send this page" / "Send everything visible".
- Target-drama choice should persist per site, so reading a long series
  isn't a per-page decision.

### Exit condition

Reading a real chapter in a normal browser on a site with **no adapter**,
clicking the extension sends the page in, and it lands in the chosen
drama with bubbles detected and translated, no worse than the same image
imported through Scanlate by hand. Plus: the endpoint refuses an
unauthenticated request, and refuses one from a non-local address.

---

## Step 27b — translations drawn over the page in place

Everything above, but the result is rendered on the page you're reading
instead of only landing in the library.

- The endpoint already returns boxes in **image coordinates**; the
  content script maps them to screen coordinates by the element's own
  scale (`clientWidth / naturalWidth`), and re-maps on resize/zoom.
- Overlay each bubble's translated text positioned over the original.
  `sample_text_style()` already estimates real font styling, so overlays
  can approximate the page instead of looking pasted on.
- Re-run when the page changes: a viewer that paginates in place
  (mangaz's own does exactly this) swaps the image without a navigation,
  so a `MutationObserver` on the reader element is needed, not a load
  handler.
- Cache by image hash, so paging back is instant and nothing is
  translated twice.
- A toggle to hide overlays, and click-to-see-original.

### Known hard parts, named rather than discovered later

- **Canvas-only viewers** give no `<img>` to anchor an overlay to;
  positioning has to fall back to the canvas element's own box.
- **Two-page spreads** and **right-to-left** reading order both affect
  which box belongs to which page.
- **Webtoon strips** are long single images; `split_webtoon_strip()`
  slices them server-side, so offsets must be recombined before overlay.
- **CSS transforms/zoom** on the viewer will break naive coordinate
  mapping.

### Exit condition

On a real chapter, translations appear over the bubbles, survive turning
the page and resizing the window, can be toggled off, and paging back
doesn't re-translate.

---

## Worth deciding in planning, not here

- **Distribution.** Loading unpacked is fine for one person; a store
  listing is a different commitment (review, policies, an update
  channel). Suggest starting unpacked.
- **Which browsers.** MV3 covers Chrome/Edge; Firefox differs enough to
  be its own work. Suggest Chrome-family first.
- **Whether 27b replaces the mangaz headless-browser path** in
  `sources/adapters/mangaz.py`. It could — the extension reaches the same
  descrambled pages far more cheaply. But the adapter path works without
  the person watching, which matters for bulk import. Probably keep both,
  and say so in `docs/content-sources.md` rather than leaving two
  overlapping mechanisms unexplained.
