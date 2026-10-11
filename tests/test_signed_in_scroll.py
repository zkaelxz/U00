"""The signed-in profile fetch scrolls a lazy-loading reader like the
anonymous rendered path does, and a no-pages failure says what was read."""
import pytest

import page_fetch
import page_scroll
from sources import adaptive
from sources.ladder import LadderResult
from sources.models import AccessTier


class _Page:
    def __init__(self, evaluate_raises=False):
        self.calls = []
        self.evaluate_raises = evaluate_raises

    def goto(self, *a, **k): self.calls.append("goto")

    def evaluate(self, js, *a, **k):
        self.calls.append("scroll" if js is page_scroll.SCROLL_THROUGH_JS else "evaluate")
        if self.evaluate_raises:
            raise RuntimeError("Execution context was destroyed")

    def wait_for_load_state(self, *a, **k): self.calls.append("settle")
    def wait_for_selector(self, *a, **k): self.calls.append("selector")
    def wait_for_timeout(self, *a, **k): self.calls.append("wait")
    def add_init_script(self, *a, **k): pass
    def on(self, *a, **k): pass

    def content(self):
        self.calls.append("content")
        return "<html><body><img src='a.jpg'></body></html>"


class _Context:
    def __init__(self, page):
        self.page = page

    def route(self, *a, **k): pass
    def new_page(self): return self.page
    def close(self): pass


class _PW:
    def stop(self): pass


def _fetch(tmp_path, page):
    def launcher(profile_dir, headless):
        return _PW(), _Context(page)
    return page_fetch.fetch_with_profile("https://public.example/c", str(tmp_path / "p"),
                                         wait_ms=0, launcher=launcher)


def test_signed_in_fetch_scrolls_once_before_reading_content(tmp_path):
    page = _Page()
    html, _ = _fetch(tmp_path, page)
    assert page.calls.count("scroll") == 1
    assert page.calls.index("scroll") < page.calls.index("content")
    assert "<img" in html


def test_scroll_failure_is_swallowed(tmp_path):
    page = _Page(evaluate_raises=True)
    html, _ = _fetch(tmp_path, page)
    assert "content" in page.calls and "<img" in html


def test_both_fetch_paths_use_the_shared_helper():
    import inspect
    for fn in (page_fetch._rendered_page, page_fetch.fetch_with_profile):
        assert "scroll_through_and_settle" in inspect.getsource(fn)
    assert not hasattr(page_fetch, "_SCROLL_THROUGH_JS")
    assert page_scroll.scroll_through_and_settle(_Page(), 1000) is True
    assert page_scroll.scroll_through_and_settle(_Page(evaluate_raises=True), 1000) is False


@pytest.mark.parametrize("tier,label,scroll", [
    (AccessTier.STATIC_HTTP, "anonymous", "not run"),
    (AccessTier.RENDERED_BROWSER, "browser", "attempted"),
    (AccessTier.AUTHENTICATED_BROWSER, "signed-in", "attempted"),
])
def test_render_diagnostic_states_tier_scroll_and_counts_only(tier, label, scroll):
    html = ("<img src='https://cdn.example/p.jpg?token=SECRETTOKEN'>"
            "<IMG data-src='/x'><p>no more</p>")
    lr = LadderResult(url="https://manga.example/c", tier=tier.value, html=html)
    line = adaptive.render_diagnostic(lr, 0)
    assert f"{label} tier" in line and f"scroll step {scroll}" in line
    assert "2 <img> tag(s)" in line and "0 candidate image URL(s)" in line
    assert "SECRETTOKEN" not in line and "cdn.example" not in line
