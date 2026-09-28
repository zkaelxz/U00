"""
extension/verify_end_to_end.py -- a real check of the browser extension.

**Not part of the test suite, on purpose.** This project's tests are
mocked throughout and CI has no browser, so a test that drives Chromium
does not belong in `tests/`. But everything in `extension/` is JavaScript
the Python suite can never execute, and `tests/test_extension_manifest.py`
can only check its *shape*. This script is what actually proves it runs.

Run it by hand after touching anything in `extension/` or
`page_server.py`:

    python extension/verify_end_to_end.py

It needs Playwright with a full Chromium build (the headless *shell*
cannot load extensions). It touches no real site and needs no API key:
`scanlate`'s detect/translate are faked, everything else -- the endpoint,
the extension, the browser, the token exchange -- is real.

What it asserts:

  * the service worker registers and the options page reaches the
    endpoint with the real token,
  * a normal `<img>` **and** a `blob:`-backed one both translate end to
    end and land in the library (the `blob:` case is the whole point:
    it's the content an adapter structurally cannot reach),
  * overlay boxes land where the app's image-pixel coordinates say they
    should, at the element's own scale, and still do after a resize,
  * click-to-see-original and the overlay toggle work,
  * the same image appearing twice is sent once, not once per element.
"""
import base64
import functools
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CHROMIUM_CANDIDATES = [
    os.environ.get("BAIHE_CHROMIUM", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
]

failures = []
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_chromium():
    for path in CHROMIUM_CANDIDATES:
        if path and os.path.exists(path):
            return path
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if path and os.path.exists(path) and "headless_shell" not in path:
                return path
    except Exception:
        pass
    return None


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright isn't installed (pip install playwright)")
        return 0
    chromium = find_chromium()
    if not chromium:
        print("SKIP: no full Chromium build found. The headless shell can't load "
              "extensions; set BAIHE_CHROMIUM to a real chrome binary.")
        return 0

    import db
    # Deliberately NOT under extension/: Chromium loads that whole folder
    # as the unpacked extension, so a browser profile and a library
    # sitting inside it made the first run pass and every later one fail
    # with "the service worker never registered".
    workdir = tempfile.mkdtemp(prefix="baihe-ext-verify-")
    print(f"working in {workdir}")
    db.configure_library_dir(os.path.join(workdir, "library"))
    db.init_db()

    import page_server
    import scanlate
    import translate_engines

    calls = []

    def fake_detect(image_path, source_language, **kwargs):
        calls.append(("detect", os.path.basename(image_path), source_language))
        return ([
            {"x": 40, "y": 60, "w": 220, "h": 90, "source_text": "こんにちは",
             "translated_text": "", "kind": "bubble", "font_category": "regular",
             "reading_order": 0},
            {"x": 300, "y": 500, "w": 180, "h": 70, "source_text": "またね",
             "translated_text": "", "kind": "narration", "font_category": "bold",
             "reading_order": 1},
        ], [])

    def fake_translate(bubbles, engine, drama_meta, **kwargs):
        calls.append(("translate", len(bubbles)))
        for b, t in zip(bubbles, ["HELLO THERE", "SEE YOU LATER"]):
            b["translated_text"] = t
        return "ctx"

    scanlate.detect_and_ocr_page = fake_detect
    scanlate.translate_page_bubbles = fake_translate
    translate_engines.get_engine = lambda *a, **kw: object()
    page_server.set_translation_config(engine="test_offline", api_key="x")

    server_port = free_port()
    token = page_server.load_or_create_token()
    threading.Thread(target=lambda: page_server._serve(server_port), daemon=True).start()
    drama_id = db.create_drama(title_en="Extension check", media_type="manga",
                               source_language="ja")

    # -- a page to read, with the hard case on it ----------------------
    from PIL import Image

    def png(shade, size=(800, 1200)):
        img = Image.new("RGB", size, shade)
        for i in range(0, size[0], 40):          # real content, so the
            for j in range(0, size[1], 40):      # two pages differ
                if (i // 40 + j // 40 + shade[0]) % 3 == 0:
                    img.paste((shade[0] // 2, 30, 60), (i, j, i + 20, j + 20))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    page_a, page_b, icon = png((250, 248, 240)), png((200, 230, 250)), png((90, 90, 90), (64, 64))
    site_dir = os.path.join(workdir, "site")
    os.makedirs(site_dir, exist_ok=True)
    with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(f"""<!doctype html><html><head><meta charset=utf-8><title>reader</title>
</head><body style="margin:0"><h1>chapter 1</h1>
<img id="plain" src="data:image/png;base64,{page_a}" style="width:400px">
<img id="twice" src="data:image/png;base64,{page_a}" style="width:200px">
<img id="viaBlob" style="width:400px">
<img id="icon" src="data:image/png;base64,{icon}" width="32">
<script>
// A blob:-backed page image: it exists only inside this tab, which is
// exactly the case a source adapter cannot reach.
const bytes = Uint8Array.from(atob("{page_b}"), c => c.charCodeAt(0));
document.getElementById("viaBlob").src =
  URL.createObjectURL(new Blob([bytes], {{type: "image/png"}}));
</script></body></html>""")

    site_port = free_port()
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    handler = functools.partial(SimpleHTTPRequestHandler, directory=site_dir)
    site = ThreadingHTTPServer(("127.0.0.1", site_port), handler)
    site.serve_forever_thread = threading.Thread(target=site.serve_forever, daemon=True)
    site.serve_forever_thread.start()
    site_url = f"http://127.0.0.1:{site_port}/"

    profile = os.path.join(workdir, "profile")
    drive = """async ([url, message]) => {
                 const tabs = await chrome.tabs.query({});
                 const tab = tabs.find(t => t.url && t.url.startsWith(url));
                 if (!tab) return {error: 'test tab not found'};
                 return await chrome.tabs.sendMessage(tab.id, message);
               }"""

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile, headless=False, executable_path=chromium,
            args=["--headless=new", "--no-sandbox",
                  f"--disable-extensions-except={HERE}", f"--load-extension={HERE}"])
        try:
            # An MV3 service worker starts lazily, so it can take a while
            # to appear and may not have appeared yet when we look. Wait
            # generously, then fall back to reading the id off
            # chrome://extensions, which is authoritative either way.
            worker = None
            for _ in range(300):
                if context.service_workers:
                    worker = context.service_workers[0]
                    break
                time.sleep(0.1)
            ext_id = worker.url.split("/")[2] if worker else None
            if not ext_id:
                probe = context.new_page()
                probe.goto("chrome://extensions/")
                probe.wait_for_timeout(1500)
                listed = probe.evaluate(
                    """() => {
                         const m = document.querySelector('extensions-manager');
                         const list = m && m.shadowRoot.querySelector('extensions-item-list');
                         if (!list) return [];
                         return [...list.shadowRoot.querySelectorAll('extensions-item')]
                           .map(e => e.id);
                       }""")
                probe.close()
                ext_id = listed[0] if listed else None
            check("the extension loads and exposes a service worker", bool(ext_id),
                  ext_id or "no extension id found")
            if not ext_id:
                return 1

            options = context.new_page()
            options.goto(f"chrome-extension://{ext_id}/options.html")
            options.fill("#token", token)
            options.fill("#port", str(server_port))
            options.click("#save")
            options.wait_for_function(
                "() => /Connected|bad/.test(document.getElementById('status').textContent)"
                " || document.getElementById('status').classList.contains('bad')",
                timeout=20000)
            status = options.text_content("#status")
            check("the options page reaches the endpoint with the real token",
                  "Connected" in status, status)

            page = context.new_page()
            page.goto(site_url + "index.html")
            page.wait_for_function("() => document.getElementById('viaBlob').naturalWidth > 0")

            options.evaluate(
                """async ([url]) => {
                     const tabs = await chrome.tabs.query({});
                     const tab = tabs.find(t => t.url && t.url.startsWith(url));
                     await chrome.scripting.executeScript(
                       {target: {tabId: tab.id}, files: ['content.js']});
                   }""", [site_url])

            # A real page made to look like a verification interstitial:
            # translateVisible must recognize and report it, not silently
            # send whatever image-sized elements happen to be on it for
            # OCR/translation.
            original_title = page.title()
            page.evaluate("() => { document.title = 'Just a moment...'; }")
            challenge_result = options.evaluate(drive, [site_url, {
                "type": "translateVisible", "dramaId": drama_id, "store": True, "all": True}])
            check("a verification/CAPTCHA-looking page is recognized and refused, not translated",
                  bool(challenge_result) and challenge_result.get("code") == "CHALLENGE_DETECTED"
                  and challenge_result.get("ok") is False,
                  json.dumps(challenge_result)[:200])
            page.evaluate("(t) => { document.title = t; }", original_title)

            result = options.evaluate(drive, [site_url, {
                "type": "translateVisible", "dramaId": drama_id, "store": True, "all": True}])
            check("translating everything visible succeeds",
                  bool(result and result.get("ok")), json.dumps(result)[:200])
            if not (result and result.get("ok")):
                return 1

            pages = result["data"].get("pages") or []
            # Two distinct images: the plain one and the blob:-backed one.
            # `twice` is the same bytes as `plain`, so it must NOT be a
            # third send.
            check("the same image on the page twice is sent once, not twice",
                  len(pages) == 2, f"{len(pages)} page(s) sent")
            check("a blob:-backed page image translates end to end",
                  len(db.list_pages(drama_id)) == 2,
                  f"{len(db.list_pages(drama_id))} page(s) in the library")
            check("the drama's own source language is used",
                  all(c[2] == "ja" for c in calls if c[0] == "detect"))

            page.wait_for_selector(".baihe-box", timeout=20000)
            overlay = page.evaluate(
                """() => {
                     const boxes = [...document.querySelectorAll('.baihe-box')];
                     const img = document.getElementById('plain').getBoundingClientRect();
                     const first = boxes[0].getBoundingClientRect();
                     const scale = img.width / 800;
                     return {texts: boxes.map(b => b.textContent),
                             left: first.left, width: first.width,
                             expectedLeft: img.left + 40 * scale,
                             expectedWidth: 220 * scale};
                   }""")
            check("the translation is drawn over the page",
                  "HELLO THERE" in overlay["texts"], str(overlay["texts"][:3]))
            check("overlay boxes map image pixels by the element's own scale",
                  abs(overlay["left"] - overlay["expectedLeft"]) < 1.5
                  and abs(overlay["width"] - overlay["expectedWidth"]) < 1.5,
                  f"left {overlay['left']} vs {overlay['expectedLeft']}, "
                  f"width {overlay['width']} vs {overlay['expectedWidth']}")

            page.click(".baihe-box")
            check("clicking a bubble shows the original text",
                  page.evaluate("() => document.querySelector('.baihe-box').textContent")
                  == "こんにちは")
            page.click(".baihe-box")

            toggled = options.evaluate(drive, [site_url, {"type": "toggleOverlays"}])
            hidden = page.evaluate(
                "() => document.querySelector('.baihe-layer')"
                ".classList.contains('baihe-hidden')")
            check("the overlay toggle hides the translations",
                  bool(toggled and toggled.get("ok")) and hidden is True)
            options.evaluate(drive, [site_url, {"type": "setOverlays", "visible": True}])

            page.set_viewport_size({"width": 700, "height": 900})
            page.wait_for_timeout(400)
            resized = page.evaluate(
                """() => {
                     const img = document.getElementById('plain').getBoundingClientRect();
                     const box = document.querySelector('.baihe-box').getBoundingClientRect();
                     const scale = img.width / 800;
                     return {left: box.left, expectedLeft: img.left + 40 * scale,
                             width: box.width, expectedWidth: 220 * scale};
                   }""")
            check("boxes re-map after a resize instead of drifting",
                  abs(resized["left"] - resized["expectedLeft"]) < 1.5
                  and abs(resized["width"] - resized["expectedWidth"]) < 1.5,
                  f"left {resized['left']} vs {resized['expectedLeft']}")

            # A second run must come from the cache, not re-translate.
            before = len([c for c in calls if c[0] == "translate"])
            options.evaluate(drive, [site_url, {
                "type": "translateVisible", "dramaId": drama_id, "store": True, "all": True}])
            check("a page already translated is served from the cache",
                  len([c for c in calls if c[0] == "translate"]) == before,
                  f"{len([c for c in calls if c[0] == 'translate'])} translate call(s) total")
        finally:
            context.close()
            site.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
        return 1
    print(f"all {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
