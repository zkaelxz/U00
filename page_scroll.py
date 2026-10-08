"""
page_scroll.py -- the scroll-through-then-settle step shared by every
browser fetch that reads a fully loaded page (anonymous rendered pages and
the signed-in profile), so the two cannot drift apart.
"""

# Readers that load each page image as it scrolls into view fill in
# nothing for a single jump to the bottom: step down about a screen at a
# time (bounded to ~10 s), then land at the bottom as before.
SCROLL_THROUGH_JS = """
async () => {
    let y = 0;
    for (let i = 0; i < 40; i++) {
        y += Math.max(window.innerHeight * 0.9, 400);
        window.scrollTo(0, y);
        await new Promise(r => setTimeout(r, 250));
        if (y >= document.documentElement.scrollHeight) break;
    }
    window.scrollTo(0, document.body.scrollHeight);
}
"""


def scroll_through_and_settle(page, timeout_ms: int) -> bool:
    """Scrolls the page top to bottom, then waits for the network to go
    quiet. True when both steps completed.

    A scroll can itself trigger a navigation (a responsive-redirect script
    reacting to the resulting resize) and a lazy loader can keep the
    network busy past the timeout; neither is fatal, so the caller just
    reads whatever rendered."""
    try:
        page.evaluate(SCROLL_THROUGH_JS)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        return False
    return True
