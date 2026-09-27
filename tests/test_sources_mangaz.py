"""
tests/test_sources_mangaz.py -- Step 23l: the mangaz.com adapter.
Metadata selectors were checked against the live site while building the
adapter (see sources/adapters/mangaz.py's module docstring); the RSA+AES
page-decryption flow is exercised end-to-end here against a mocked
ticket/key-exchange fixture -- a real keypair, a real RSA/PKCS1v1.5
encrypt of a real AES key, and a real AES-CBC/PKCS7 encrypt of a real
JSON manifest, all built by this test file itself as the exact inverse of
what the adapter decrypts -- per this step's own roadmap exit condition
("without a hardcoded plaintext shortcut standing in for the real
decrypt step"). No request ever reaches the real site.
"""
import base64
import json

import pytest

from sources.adapters import mangaz
from sources.http import Response
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = mangaz.BASE_URL
VIRGO = mangaz.VIRGO_HOST

# `title/addpage_renewal` is an AJAX partial: confirmed live (Step 25v)
# that the real site returns a bare sequence of `<li>` cards with no
# wrapping `<html>`/`<body>` at all, not a full page or a `<ul>`-wrapped
# list -- this fixture matches that real shape rather than a guessed one.
LATEST_HTML = ("<li><div class='listBox'><div class='listBoxImg'><a href='/series/detail/101'>"
              "<img data-src='https://img.example.invalid/101.webp'></a></div>"
              "<div class='listBoxDetail'><h4><a href='/series/detail/101'>Test Series</a></h4>"
              "<p class='author'>Author One</p></div></div></li>"
              "<li><div class='iconConsent'>pending</div>"
              "<h4><a href='/series/detail/999'>Hidden</a></h4></li>")

SERIES_HTML = ("<html><body><div class='itemList'><div class='itemSort'><ul>"
              "<li class='item series_sort'><a href='/book/detail/103'><img></a>"
              "<a href='/book/detail/103'><span class='bookNum title'>3巻</span></a></li>"
              "<li class='item series_sort'><a href='/book/detail/102'><img></a>"
              "<a href='/book/detail/102'><span class='bookNum title'>2巻</span></a></li>"
              "<li class='item series_sort'><a href='/book/detail/101'><img></a>"
              "<a href='/book/detail/101'><span class='bookNum title'>1巻</span></a></li>"
              "</ul></div></div></body></html>")

BOOK_DETAIL_HTML = ("<html><body>"
                    "<h1 class='GA4_booktitle'>Test Series 1</h1>"
                    "<div class='detailFlex'><div class='detailLeft'><div>"
                    "<div class='detailCover'><a class='ga'>"
                    "<img src='https://img.example.invalid/cover.webp'></a></div>"
                    "</div></div></div>"
                    "<ul class='detailAuthor'>"
                    "<li>著作者：<span><a href='/authors/detail/1'>Author One</a></span></li>"
                    "<li>作画：<span><a href='/authors/detail/2'>Artist One</a></span></li>"
                    "</ul>"
                    "<p class='wordbreak'>A test description.</p>"
                    "<div class='inductionTags'><a href='/g1'>Romance</a><a href='/g2'>Comedy</a></div>"
                    "</body></html>")

EMPTY_BOOK_DETAIL_HTML = "<html><body><div class='not-a-book-page'></div></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("mangaz", t, clock, max_retries=kw.pop("max_retries", 0))
    return mangaz.MangazSource(client=client, **kw), t


class TestSearch:
    def test_empty_query_hits_latest_updates(self):
        url = f"{BASE}/title/addpage_renewal?type=official&sort=new&page=1"
        a, t = _adapter({url: html(LATEST_HTML)})
        results = a.search("")
        assert [r.series_id for r in results] == ["101"]
        assert results[0].title == "Test Series"
        assert results[0].cover_url == "https://img.example.invalid/101.webp"
        assert t.urls() == [url]

    def test_query_hits_the_search_endpoint(self):
        url = f"{BASE}/title/addpage_renewal?query=test&page=1"
        a, t = _adapter({url: html(LATEST_HTML)})
        a.search("test")
        assert t.urls() == [url]

    def test_license_pending_results_are_skipped(self):
        url = f"{BASE}/title/addpage_renewal?type=official&sort=new&page=1"
        a, t = _adapter({url: html(LATEST_HTML)})
        results = a.search("")
        assert "999" not in [r.series_id for r in results]


class TestSeries:
    def test_parses_title_authors_description_genres_status_cover(self):
        a, t = _adapter({f"{BASE}/book/detail/101": html(BOOK_DETAIL_HTML)})
        info = a.get_series("101")
        assert info.title == "Test Series 1"
        assert info.authors == ["Author One"]
        assert info.description == "A test description."
        assert info.genres == ["Romance", "Comedy"]
        assert info.status == "unknown"   # module docstring: real status markers not found live
        assert info.cover_url == "https://img.example.invalid/cover.webp"

    def test_layout_changed_when_title_is_missing(self):
        a, t = _adapter({f"{BASE}/book/detail/101": html(EMPTY_BOOK_DETAIL_HTML)})
        with pytest.raises(SourceError):
            a.get_series("101")


class TestChapters:
    def test_parses_volumes_in_ascending_order(self):
        a, t = _adapter({f"{BASE}/series/detail/101": html(SERIES_HTML)})
        chapters = a.get_chapters("101")
        assert [c.chapter_id for c in chapters] == ["101", "102", "103"]
        assert [c.title for c in chapters] == ["1巻", "2巻", "3巻"]

    def test_layout_changed_when_no_volumes(self):
        a, t = _adapter({f"{BASE}/series/detail/101":
                         html("<html><body><div class='itemList'></div></body></html>")})
        with pytest.raises(SourceError):
            a.get_chapters("101")


class TestPageDecryptionUnit:
    """Direct unit coverage of the crypto helpers, isolated from the
    network flow -- a real keypair, a real RSA/PKCS1v1.5-encrypted AES
    key, and a real AES-CBC/PKCS7-encrypted manifest."""

    def _encrypt_manifest(self, private_key, manifest: dict) -> dict:
        cryptography = pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        import os
        plain = json.dumps(manifest).encode("utf-8")
        aes_key, iv = os.urandom(32), os.urandom(16)
        pad_len = 16 - (len(plain) % 16)
        padded = plain + bytes([pad_len]) * pad_len
        encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        ek = private_key.public_key().encrypt(aes_key, padding.PKCS1v15())
        return {"bi": base64.b64encode(iv).decode(), "ek": base64.b64encode(ek).decode(),
               "data": base64.b64encode(ciphertext).decode()}

    def test_round_trips_a_real_encrypted_manifest(self):
        pytest.importorskip("cryptography")
        private_key = mangaz._generate_rsa_keypair()
        manifest = {"Images": [{"file": "001abc.jpg"}, {"file": "002def.jpg"}],
                   "Location": {"base": "https://img.example.invalid/", "st": "book/101/"}}
        encrypted = self._encrypt_manifest(private_key, manifest)
        decrypted = mangaz.decrypt_page_manifest(private_key, encrypted)
        assert decrypted == manifest

    def test_layout_changed_on_malformed_encrypted_response(self):
        pytest.importorskip("cryptography")
        private_key = mangaz._generate_rsa_keypair()
        with pytest.raises(SourceError):
            mangaz.decrypt_page_manifest(private_key, {"not": "the right shape"})

    def test_layout_changed_when_location_is_truthy_but_wrongly_shaped(self):
        """Step 25v bug 3: a truthy-but-wrong-shape Location (a bare string
        instead of {base, st}) used to pass the old `not manifest.get(...)`
        check and crash `get_pages` with an uncaught AttributeError instead
        of a clean adapter error."""
        pytest.importorskip("cryptography")
        private_key = mangaz._generate_rsa_keypair()
        manifest = {"Images": [{"file": "a.jpg"}], "Location": "not-a-dict"}
        encrypted = self._encrypt_manifest(private_key, manifest)
        with pytest.raises(SourceError):
            mangaz.decrypt_page_manifest(private_key, encrypted)

    def test_layout_changed_when_images_are_truthy_but_wrongly_shaped(self):
        """Same bug, the other field: Images present as a list of strings
        instead of a list of {file} dicts."""
        pytest.importorskip("cryptography")
        private_key = mangaz._generate_rsa_keypair()
        manifest = {"Images": ["001abc.jpg"], "Location": {"base": "x", "st": "y"}}
        encrypted = self._encrypt_manifest(private_key, manifest)
        with pytest.raises(SourceError):
            mangaz.decrypt_page_manifest(private_key, encrypted)

    def test_wrong_key_produces_a_clean_failure_not_garbage(self):
        pytest.importorskip("cryptography")
        private_key = mangaz._generate_rsa_keypair()
        wrong_key = mangaz._generate_rsa_keypair()
        manifest = {"Images": [{"file": "a.jpg"}], "Location": {"base": "x", "st": "y"}}
        encrypted = self._encrypt_manifest(private_key, manifest)
        with pytest.raises(SourceError):
            mangaz.decrypt_page_manifest(wrong_key, encrypted)


def _viewer_html(orders, base="https://img.example.invalid/Books/11/114/",
                 scramble_dir="anne_Dtest"):
    """The real viewer page shape: its whole document as base64 in a
    `#doc` element, exactly what the site's own vw6 viewer reads with
    `JSON.parse(window.atob($("#doc").text()))`."""
    doc = {"Location": {"viewer": "anne", "base": base, "scramble_dir": scramble_dir},
           "Book": {"baid": "114"}, "Images": [], "Orders": orders,
           "User": {"login": False}}
    encoded = base64.b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")
    return html(f"<html><body><div id='doc'>{encoded}</div></body></html>")


ORDERS = [{"no": 0, "name": "001abc.jpg", "side": "right", "pair_no": 1},
          {"no": 1, "name": "002def.jpg", "side": "left", "pair_no": 0}]


class _FakeViewerPage:
    """Stands in for the site's own reader: answers the adapter's probes
    the way a real, working viewer would, and hands back a blob per page
    it has been moved to -- never revealing how a real page is
    descrambled, because the adapter never does that itself."""

    def __init__(self, page_blobs, ready=True, slow_pages=()):
        self.page_blobs = page_blobs          # {page no: bytes}
        self.ready = ready
        # Pages that don't appear on their first visit, the way a real
        # viewer that hasn't finished drawing one yet behaves.
        self.slow_pages = set(slow_pages)
        self.moved_to = []
        self._displayed = {}

    def evaluate(self, js, arg=None):
        from page_fetch import _KEPT_BLOBS_JS
        # Exact matches first: the readiness probe mentions movePage too.
        if js.strip() == mangaz._VIEWER_READY_JS.strip():
            return self.ready
        if js.strip() == mangaz._VIEWER_PAGE_IMAGES_JS.strip():
            return dict(self._displayed)
        if js.strip() == _KEPT_BLOBS_JS.strip():
            return {f"blob:https://viewer.invalid/{no}": base64.b64encode(data).decode("ascii")
                    for no, data in self.page_blobs.items()}
        if "movePage" in js:
            first_visit = arg not in self.moved_to
            self.moved_to.append(arg)
            if arg in self.page_blobs and not (first_visit and arg in self.slow_pages):
                self._displayed[str(arg)] = f"blob:https://viewer.invalid/{arg}"
            return None
        raise AssertionError(f"unexpected evaluate(): {js[:60]}")

    def wait_for_timeout(self, ms):
        pass


def _fake_session(page):
    from contextlib import contextmanager

    @contextmanager
    def session(url, **kw):
        page.opened_url = url
        page.opened_kw = kw
        yield page
    return session


class TestPagesFromTheViewerManifest:
    """The site's real current protocol (confirmed live 2026-09-27): the
    viewer page carries its own manifest, and the decommissioned
    RSA+AES `docx` endpoint is not involved at all."""

    def test_pages_come_from_the_embedded_doc_manifest(self):
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == [
            "https://img.example.invalid/Books/11/114/anne_Dtest/001abc.jpg",
            "https://img.example.invalid/Books/11/114/anne_Dtest/002def.jpg",
        ]
        # No key exchange, no ticket, no docx request -- just the viewer page.
        assert [c["url"] for c in t.calls] == [f"https://{VIRGO}/virgo/view/114"]

    def test_missing_manifest_is_reported_plainly(self):
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114":
                         html("<html><body>no doc element</body></html>")})
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        with pytest.raises(SourceError):
            a.get_pages(chapter)

    def test_download_page_serves_what_the_sites_own_viewer_descrambled(self):
        page = _FakeViewerPage({0: b"page-zero-bytes", 1: b"page-one-bytes"})
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        a._viewer_session = _fake_session(page)
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        assert a.download_page(refs[0]) == (b"page-zero-bytes", ".jpg")
        # One viewer session covers the whole book: the second page is
        # already cached, so nothing further is driven for it.
        moved = list(page.moved_to)
        assert a.download_page(refs[1]) == (b"page-one-bytes", ".jpg")
        assert page.moved_to == moved
        assert page.opened_kw.get("keep_blobs") is True

    def test_scrambled_page_file_is_never_downloaded_directly(self):
        page = _FakeViewerPage({0: b"page-zero-bytes", 1: b"page-one-bytes"})
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        a._viewer_session = _fake_session(page)
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        a.download_page(refs[0])
        # The .jpg the manifest points at is tile-scrambled; fetching it
        # would yield a strip of tiles, so it must never be requested.
        assert not [c for c in t.calls if "anne_Dtest" in c["url"]]

    def test_a_page_the_viewer_was_slow_to_draw_is_revisited(self):
        """A live run left 5 of 43 pages behind simply because the viewer
        hadn't finished drawing them when their turn came round. Slow is
        not missing -- each straggler gets one more visit."""
        page = _FakeViewerPage({0: b"page-zero-bytes", 1: b"page-one-bytes"}, slow_pages={1})
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        a._viewer_session = _fake_session(page)
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        assert a.download_page(refs[1]) == (b"page-one-bytes", ".jpg")
        assert page.moved_to.count(1) > 1      # revisited, not given up on

    def test_browser_that_cannot_reach_the_reader_fails_cleanly(self):
        """A live run hit net::ERR_TOO_MANY_RETRIES navigating to the
        reader. That has to surface as a plain reason like every other
        failure here, not a raw Playwright traceback out of the adapter."""
        from contextlib import contextmanager
        from sources.models import FailureReason

        @contextmanager
        def broken_session(url, **kw):
            raise RuntimeError("net::ERR_TOO_MANY_RETRIES")
            yield  # pragma: no cover

        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        a._viewer_session = broken_session
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        with pytest.raises(SourceError) as exc_info:
            a.download_page(refs[0])
        assert exc_info.value.reason == FailureReason.ENCRYPTED_RESOURCE
        assert "RuntimeError" in str(exc_info.value)

    def test_viewer_that_never_comes_up_is_refused_not_guessed(self):
        page = _FakeViewerPage({}, ready=False)
        a, t = _adapter({f"https://{VIRGO}/virgo/view/114": _viewer_html(ORDERS)})
        a._viewer_session = _fake_session(page)
        chapter = ChapterInfo("mangaz", "101", "114", "14巻", f"{BASE}/book/detail/114")
        refs = a.get_pages(chapter)
        with pytest.raises(SourceError) as exc_info:
            a.download_page(refs[0])
        from sources.models import FailureReason
        assert exc_info.value.reason == FailureReason.ENCRYPTED_RESOURCE


class TestLegacyKeyExchangeHelpers:
    """The RSA+AES helpers are kept (another legacy site needing a
    sub-1024-bit key could reuse them) but the site itself no longer uses
    this flow -- see the module docstring. These cover the helpers
    directly, not a page fetch."""

    def test_no_second_key_exchange_needed_but_ticket_is_per_book(self):
        # Confirms the adapter re-requests a ticket per book id (the real
        # site's own per-content ticketing) rather than assuming one
        # ticket works for any chapter.
        clock = FakeClock()
        t = ScriptedTransport({
            f"https://{VIRGO}/virgo/app.js": html('var __serial = "abc123";'),
            f"https://{VIRGO}/virgo/view/114": Response(200, {}, b"", "",
                                                         cookies={"virgo!__ticket": "tick-1"}),
        }, clock)
        client = make_client("mangaz", t, clock, max_retries=0)
        a = mangaz.MangazSource(client=client)
        assert a._fetch_ticket("114") == "tick-1"
        # The serial is cached across calls (module docstring: lazy, once
        # per instance) -- only one app.js fetch total.
        a._fetch_serial()
        a._fetch_serial()
        assert len([c for c in t.calls if "app.js" in c["url"]]) == 1

    def test_missing_ticket_cookie_is_reported_plainly(self):
        clock = FakeClock()
        t = ScriptedTransport({f"https://{VIRGO}/virgo/view/114": Response(200, {}, b"", "")}, clock)
        client = make_client("mangaz", t, clock, max_retries=0)
        a = mangaz.MangazSource(client=client)
        with pytest.raises(SourceError):
            a._fetch_ticket("114")


class TestCryptoUnavailable:
    def test_missing_cryptography_is_reported_as_not_installed(self, monkeypatch):
        from sources.models import FailureReason

        def boom(*a, **kw):
            raise ImportError("no cryptography")
        monkeypatch.setattr(mangaz, "_generate_rsa_keypair", boom)
        a, t = _adapter({})
        with pytest.raises(SourceError) as exc_info:
            a._keys()
        assert exc_info.value.reason == FailureReason.NOT_INSTALLED


class TestParseUrl:
    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/series/detail/101")
        assert kind == "series"
        assert series_id == "101"

    def test_book_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/book/detail/114")
        assert kind == "series"
        assert series_id == "114"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url(f"{BASE}/series/detail/101")
        assert adapter is not None
        assert adapter.name == "mangaz"
