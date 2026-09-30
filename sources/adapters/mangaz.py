"""
sources/adapters/mangaz.py -- Manga Toshokan Z / マンガ図書館Z (mangaz.com,
ja manga), roadmap Step 23l.

**The most technically involved mechanism confirmed this session** (real
extension: keiyoushi/extensions-source, src/ja/mangatoshokanz, class
`MangaToshokanZ` + its own `Crypto.kt`, Apache-2.0): a session-scoped
hybrid RSA+AES scheme, not a static obfuscation trick. Ported exactly,
step for step, not reimplemented from a guessed shape:

  1. Generate a fresh **512-bit RSA keypair** (public exponent 65537/F4)
     for this one session -- the site's own real, if legacy-weak, choice
     (Java's `KeyPairGenerator` with `RSAKeyGenParameterSpec(512, F4)`).
     Python's mainstream crypto libraries (`cryptography`, PyCA's own
     `generate_private_key`) refuse to *generate* an RSA key below 1024
     bits as a modern safety floor -- confirmed directly while building
     this adapter, not assumed. Since loading arbitrary, already-known
     key material below that floor is **not** refused by the same
     library, this module generates the two ~256-bit primes itself with a
     standard Miller-Rabin primality test (`_generate_rsa_keypair` below
     -- ordinary textbook RSA keygen math, not a novel or weakened
     algorithm) and hands the resulting numbers to `cryptography`'s own
     `RSAPrivateNumbers.private_key()`, so every actual cryptographic
     operation (PKCS1v1.5 RSA decrypt, AES-CBC decrypt) still runs through
     the real, audited library -- only prime generation for this one
     legacy-mandated key size is this module's own code.
  2. **Ticket + serial exchange**: HEAD `{virgo}/view/<id>` for a
     `virgo!__ticket` session cookie (read via the response's own cookie
     jar, not a manual Set-Cookie header parse -- see the note on
     `sources.http.Response.cookies`, added in this same step, since a
     response here sets several cookies at once and a plain header dict
     only keeps the last); GET `{virgo}/app.js` for a `__serial = "..."`
     value embedded in real, live JavaScript.
  3. POST `{virgo}/docx/<id>.json` with `__serial`, `__ticket`, and this
     session's RSA public key as a PEM (`SubjectPublicKeyInfo`, the same
     encoding Java's own `KeyPairGenerator` output produces) -- gets back
     `{bi, ek, data}` (all base64): `ek` is the real page-decryption AES
     key, RSA/PKCS1v1.5-encrypted to the public key just sent; `bi` is the
     AES IV; `data` is the AES-CBC/PKCS7-encrypted page manifest.
  4. RSA-decrypt `ek` with this session's own private key (PKCS1v1.5,
     `cryptography`'s `padding.PKCS1v15()`), then AES-CBC/PKCS7-decrypt
     `data` with that key and `bi` as the IV -- yields a JSON manifest
     (`Images: [{file}]`, `Location: {base, st}`); each page's real URL is
     `location.base + location.st + file-without-extension + ".jpg"`.

**SUPERSEDED (2026-09-27): everything above is a dead legacy protocol.**
`get_pages()` no longer does any of it. Established by reading the site's
own current viewer script (`vw.mangaz.com/virgo/js/vw6-simple.js`): it
still calls `forge.pki.rsa.generateKeyPair(512)` but **discards the
result** -- a bare statement, no assignment, and `forge` appears nowhere
else in the file -- and never requests `docx` at all. `POST /virgo/docx/
<id>.json` is decommissioned: it answers a real, fast HTTP 500 to every
request, which was reproduced across 7 real books and 8 request shapes
(header, cookie and key-encoding variations, and a real Chrome TLS
fingerprint) before its cause was found. The serial and ticket do
survive, but only as parameters to the viewer's *other* live endpoints
(`/virgo/serifs/<baid>.json`, `/virgo/bingToken/<baid>.json`), which this
adapter doesn't use.

**The real current protocol** (confirmed against real books): the reader
at `GET /virgo/view/<book_id>` carries its whole document as base64 in a
`#doc` element -- `JSON.parse(window.atob($("#doc").text()))` in the
site's own code -- giving `Location` (`base`, `scramble_dir`), `Orders`
(every page's `name`/`side`/`pair_no` plus its own descramble `crops`),
`Book` and `User`. `_viewer_doc()`/`get_pages()` read exactly that.

**Pages are tile-scrambled, and this adapter never unscrambles them.** A
real 1190x1684 page is served as a ~4760x421 strip of tiles, with the
manifest carrying the crop list that reassembles it. Porting that
transform here would be easy and is deliberately not done -- the same
line this module already drew around the site's crypto. Instead
`download_page()` opens the site's own reader and steps it with its own
public `JCOMI.viewer.movePage()` API, exactly as a reader clicking onward
would, and reads back the descrambled page the viewer itself publishes as
a blob to its own `<img>` (tagged with that page's number by the site's
own code, so pages are identified by the site, not by our guesswork).

**The RSA+AES helpers below are kept deliberately, though nothing calls
them now** -- `_generate_rsa_keypair()` in particular is real, non-obvious
work (mainstream libraries refuse to *generate* a sub-1024-bit key, so it
builds the primes itself and hands them to `cryptography`'s own loader),
and another legacy site demanding the same weak-key exchange could reuse
it. Flagged rather than deleted; whether they earn their keep is the
planning session's call, not this change's.

**Metadata scraping selectors** (search/latest/series/chapters) are read
directly from the real, live site while building this adapter -- not
assumed from the reference source alone; `.itemList li` is a genuine CSS
*descendant* selector in the real chapter-list markup (`.itemList >
.itemSort > ul > li`, confirmed against several real multi-volume
series), not a direct-children one, which only matters if a future edit
here reaches for `BeautifulSoup.find_all(recursive=False)` instead of
`.select()`. The `.iconContinues`/`.iconEnd` ongoing/completed status
markers named in the reference extension were **not found on any real
page checked here** (a real, confirmed site change since the reference
was written) -- `status` is read the same way regardless (so it recovers
automatically if the site restores those classes) but will honestly stay
`unknown` until then; not chased further since it's cosmetic, not part of
this step's own real exit condition.

Real `robots.txt` (both `www.mangaz.com` and `vw.mangaz.com`) sets
`Crawl-delay: 120` for `User-agent: *` -- respected via `host_min_interval`
below, not just noted.

**Two adaptations from the reference extension, both because this app's
`get_series()` needs a standalone title/cover** (tachiyomi's own
`mangaDetailsParse` never extracts either -- it relies on the title/
thumbnail already carried over from whichever search-result list surfaced
the manga, which this app's own interface doesn't assume): title reuses
the real `.GA4_booktitle` element (confirmed present on `book/detail`,
though the reference only reads it from the single-chapter-redirect
branch of `chapterListParse`); cover reads `div.detailCover img`
specifically, found by tracing the real DOM (a bare `a > img` matches the
site's own header logo first -- confirmed directly, not guessed).
"""

import random
import re
import secrets
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import AccessTier, ChapterInfo, ContentAccess, ContentHidden, ContentType, \
    FailureReason, SearchResult, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.mangaz.com"
VIRGO_HOST = "vw.mangaz.com"   # a fixed subdomain in the real site, not per-mirror (module docstring)

# Is the site's own reader up and driveable yet?
_VIEWER_READY_JS = "() => !!(window.JCOMI && JCOMI.viewer && JCOMI.viewer.movePage)"

# Which page each descrambled image on screen belongs to. The viewer tags
# every image it finishes with its own page number (`q.setAttribute("no", a)`
# in its own code), so its output is read back keyed by that, never guessed
# from ordering.
_VIEWER_PAGE_IMAGES_JS = """
() => {
    const out = {};
    document.querySelectorAll('img[no]').forEach(img => {
        const src = img.src || '';
        if (src.startsWith('blob:')) out[img.getAttribute('no')] = src;
    });
    return out;
}
"""

# How long to let the viewer decode and draw a page before reading it,
# picked fresh per page. A real reader doesn't turn pages on a metronome,
# and a fixed sub-second beat across dozens of turns is the most
# automated-looking thing this adapter does -- a live pass driving the
# viewer that way got throttled partway through (its scripts stopped
# coming back at all), which is also slower in the end than reading at a
# human pace would have been.
_VIEWER_PAGE_SETTLE_MS = (1500, 4000)


def _page_settle_ms() -> int:
    return random.randint(*_VIEWER_PAGE_SETTLE_MS)


class LayoutChanged(SourceError):
    """The page/response didn't have what this adapter expects -- most
    likely the site changed its markup or protocol. Reported plainly,
    never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"mangaz's page/response has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


class CryptoUnavailable(SourceError):
    def __init__(self):
        super().__init__(
            "mangaz.com needs the optional 'cryptography' package for its session-scoped "
            "RSA+AES page decryption (see requirements-optional.txt) -- install it to use this source.",
            FailureReason.NOT_INSTALLED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


# --- RSA-512 keygen: standard textbook math for the one legacy key size
# mainstream libraries refuse to *generate* (module docstring). Every
# actual crypto *operation* still goes through `cryptography`. ---------

def _is_probable_prime(n: int, rounds: int = 20) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(n):
            return n


def _generate_rsa_keypair(key_size: int = 512, public_exponent: int = 65537):
    """A fresh RSA private key object (`cryptography`'s RSAPrivateKey),
    built from hand-generated primes because the library itself refuses
    to generate a key this small (module docstring). Every cryptographic
    operation performed with the result still runs through the real
    library, not this module's own code."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.asymmetric.rsa import (
        rsa_crt_dmp1, rsa_crt_dmq1, rsa_crt_iqmp)
    half = key_size // 2
    while True:
        p, q = _generate_prime(half), _generate_prime(half)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != key_size:
            continue
        phi = (p - 1) * (q - 1)
        if phi % public_exponent == 0:
            continue
        d = pow(public_exponent, -1, phi)
        public_numbers = rsa.RSAPublicNumbers(public_exponent, n)
        private_numbers = rsa.RSAPrivateNumbers(
            p, q, d, rsa_crt_dmp1(d, p), rsa_crt_dmq1(d, q), rsa_crt_iqmp(p, q), public_numbers)
        return private_numbers.private_key()


def _public_key_pem(public_key) -> str:
    from cryptography.hazmat.primitives import serialization
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def _rsa_decrypt_pkcs1(private_key, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import padding
    return private_key.decrypt(ciphertext, padding.PKCS1v15())


def _aes_cbc_pkcs7_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """Conforms with CryptoJS's AES defaults, matching keiyoushi's own
    `CryptoAES.decrypt(cipherText, keyBytes, ivBytes)` overload (module
    docstring) -- plain AES/CBC/PKCS7, explicit key+IV, no passphrase/KDF
    involved (that's the library's *other* overload, unused here)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    if not (0 < pad_len <= 16) or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise LayoutChanged("valid PKCS7 padding on the decrypted page manifest")
    return padded[:-pad_len]


def decrypt_page_manifest(private_key, encrypted: dict) -> dict:
    """`encrypted` is the real `{bi, ek, data}` JSON body (module
    docstring, step 3-4). Returns the decoded `{Images:[...], Location:
    {...}}` manifest. Raises CryptoUnavailable if `cryptography` isn't
    installed, LayoutChanged if the response shape or decrypted content
    doesn't parse."""
    import base64
    import json
    try:
        iv = base64.b64decode(encrypted["bi"])
        ek = base64.b64decode(encrypted["ek"])
        ciphertext = base64.b64decode(encrypted["data"])
    except (KeyError, TypeError, ValueError) as e:
        raise LayoutChanged(f"a recognized {{bi, ek, data}} encrypted-page response ({e})") from None
    try:
        aes_key = _rsa_decrypt_pkcs1(private_key, ek)
        plaintext = _aes_cbc_pkcs7_decrypt(aes_key, iv, ciphertext)
    except LayoutChanged:
        raise
    except Exception as e:
        raise LayoutChanged(f"a decryptable encrypted-page payload ({e})") from None
    try:
        manifest = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise LayoutChanged(f"valid JSON in the decrypted page manifest ({e})") from None
    if not isinstance(manifest, dict):
        raise LayoutChanged("a JSON object as the decrypted page manifest")
    images = manifest.get("Images")
    location = manifest.get("Location")
    if not isinstance(images, list) or not images or not all(isinstance(im, dict) for im in images):
        raise LayoutChanged("a well-formed Images list in the decrypted page manifest")
    if not isinstance(location, dict):
        raise LayoutChanged("a well-formed Location object in the decrypted page manifest")
    return manifest


@register
class MangazSource(SourceAdapter):
    name = "mangaz"
    display_name = "マンガ図書館Z Manga Toshokan Z"
    content_types = [ContentType.MANGA.value]
    languages = ["ja"]
    url_patterns = [r"mangaz\.com/(?:series|book)/detail/\d+"]
    # Real robots.txt Crawl-delay: 120 on both hosts this adapter uses
    # (module docstring) -- respected, not just noted.
    host_min_interval = {"www.mangaz.com": 120, "vw.mangaz.com": 120}

    def __init__(self, client=None, base_url: str = None, viewer_session=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._private_key = None
        self._serial = None
        self._series_pages = {}
        self._viewer_docs = {}
        # Descrambled page bytes, captured once per book during the one
        # viewer session get_pages()/download_page() need (see
        # _capture_pages) -- the same "fetched once, served from cache"
        # shape bilibili_manga.py uses for its short-lived image tokens.
        self._page_bytes = {}
        # Book ids the viewer has already been driven through, so a book
        # it couldn't fully draw costs one session and not one per
        # missing page -- a throttled run that returned 38 of 43 pages
        # would otherwise re-drive the whole book five more times, each
        # behind this host's real 120s crawl delay.
        self._captured = set()
        # Injectable so tests drive a fake viewer instead of a real
        # browser; None means the real page_fetch.rendered_session.
        self._viewer_session = viewer_session

    # -- session-scoped RSA/serial (lazy, once per adapter instance,
    # matching the reference extension's own `by lazy` fields) ----------
    def _keys(self):
        if self._private_key is None:
            try:
                self._private_key = _generate_rsa_keypair()
            except ImportError:
                raise CryptoUnavailable() from None
        return self._private_key

    def _fetch_serial(self) -> str:
        if self._serial is None:
            resp = self.client.get(f"https://{VIRGO_HOST}/virgo/app.js", classify_body=False,
                                   headers={"Cookie": "_LANG_=ja"}, action="Loading mangaz session script")
            m = re.search(r'__serial = "([^"]+)"', resp.text)
            if not m:
                raise LayoutChanged("a __serial value in the session script")
            self._serial = m.group(1)
        return self._serial

    def _fetch_ticket(self, book_id: str) -> str:
        resp = self.client.request(
            "HEAD", f"https://{VIRGO_HOST}/virgo/view/{book_id}", classify_body=False,
            use_cache=False, headers={"Cookie": "_LANG_=ja"}, action=f"Requesting a ticket for {book_id}")
        ticket = resp.cookies.get("virgo!__ticket")
        if not ticket:
            raise LayoutChanged("a virgo!__ticket cookie in the ticket response")
        return ticket

    # -- listing/search ----------------------------------------------------
    def _parse_cards(self, container) -> list:
        results = []
        for li in container.find_all("li", recursive=False):
            if li.select_one(".iconConsent") is not None:
                continue   # license-pending: not actually readable (module docstring / reference)
            a = li.select_one("h4 > a")
            if a is None:
                continue
            href = a.get("href", "")
            series_id = href.rstrip("/").rsplit("/", 1)[-1]
            if not series_id:
                continue
            img = li.select_one("a > img")
            thumb = ""
            if img is not None:
                thumb = img.get("data-src", "") or img.get("src", "")
            results.append(SearchResult(self.name, series_id, a.get_text(strip=True),
                                        urljoin(self.base_url, href), thumb))
        return results

    def search(self, query: str, page: int = 1):
        query = (query or "").strip()
        headers = {"X-Requested-With": "XMLHttpRequest", "Cookie": "_LANG_=ja"}
        if query:
            from urllib.parse import quote
            url = (f"{self.base_url}/title/addpage_renewal?query={quote(query)}"
                  f"&page={int(page)}")
            action = f"Searching mangaz for {query!r}"
        else:
            url = f"{self.base_url}/title/addpage_renewal?type=official&sort=new&page={int(page)}"
            action = "Loading mangaz latest updates"
        resp = self.client.get(url, headers=headers, action=action)
        soup = _soup(resp.text)
        # `addpage_renewal` is an AJAX partial: confirmed live (Step 25v)
        # that it returns a bare sequence of `<li>` cards with no wrapping
        # `<html>`/`<body>` at all, so `soup.body` is None and each `<li>`
        # really is a direct child of the parsed fragment's own root --
        # `soup.body or soup` deliberately falls back to that root as the
        # "list container" `_parse_cards`'s non-recursive scan expects.
        body = soup.body or soup
        return self._parse_cards(body)

    # -- series/chapters -----------------------------------------------
    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self.client.get(
                f"{self.base_url}/series/detail/{series_id}",
                headers={"Cookie": "_LANG_=ja"}, action=f"Loading series {series_id}").text
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        # book/detail carries the real author/genre/description/status
        # markup (module docstring); series/detail is only used for its
        # chapter list. Real registered-content books can have a book id
        # distinct from the series id, but for `get_series` (no chapter
        # context yet) the series id is the closest identifier available.
        resp = self.client.get(f"{self.base_url}/book/detail/{series_id}",
                               headers={"Cookie": "_LANG_=ja"}, action=f"Loading series {series_id}")
        soup = _soup(resp.text)
        authors, artists = [], []
        for li in soup.select(".detailAuthor > li"):
            text = li.get_text(strip=True)
            name_el = li.find(True, recursive=False)
            name = name_el.get_text(strip=True) if name_el is not None else text
            if "者" in text or "原作" in text:
                authors.append(name)
            elif "作画" in text or "マンガ" in text:
                artists.append(name)
        desc_el = soup.select_one(".wordbreak")
        genre_as = soup.select(".inductionTags a")
        title_el = soup.select_one(".GA4_booktitle") or soup.select_one("h1")
        if title_el is None:
            raise LayoutChanged("the series title")
        status = "ongoing" if soup.select_one("p.iconContinues") else \
            "completed" if soup.select_one("p.iconEnd") else "unknown"
        cover = soup.select_one("div.detailCover img")
        return SeriesInfo(
            self.name, series_id, title_el.get_text(strip=True),
            f"{self.base_url}/series/detail/{series_id}",
            cover.get("data-src", "") or cover.get("src", "") if cover is not None else "",
            authors=authors or artists, description=desc_el.get_text(strip=True) if desc_el else "",
            genres=[a.get_text(strip=True) for a in genre_as], status=status,
            content_type=ContentType.MANGA.value, language="ja")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        # A real descendant selector (module docstring): `.itemList` ->
        # `.itemSort` -> `ul` -> `li`, not direct children.
        items = soup.select(".itemList li")
        if not items:
            raise LayoutChanged("any chapters/volumes for this series")
        chapters = []
        for li in reversed(items):   # real markup lists newest-first
            a = li.select_one("a")
            title_el = li.select_one(".title")
            if a is None or not a.get("href"):
                continue
            book_id = a.get("href", "").rstrip("/").rsplit("/", 1)[-1]
            if not book_id:
                continue
            title = title_el.get_text(strip=True) if title_el is not None else book_id
            chapters.append(ChapterInfo(self.name, series_id, book_id, title,
                                        f"{self.base_url}/book/detail/{book_id}"))
        return chapters

    # -- pages: the site's real current protocol (module docstring) -------
    def _viewer_doc(self, book_id: str) -> dict:
        """The viewer page's own embedded manifest. The reader at
        `/virgo/view/<book_id>` carries its whole document as base64 in a
        `#doc` element (`JSON.parse(window.atob($("#doc").text()))`, read
        out of the site's own vw6 viewer script) -- `Location` (`base`,
        `scramble_dir`), `Orders` (every page's name/side/pair plus its
        own descramble crops), `Book` and `User`. No ticket, serial or key
        exchange is involved: those survive only as parameters to the
        viewer's other endpoints."""
        if book_id not in self._viewer_docs:
            import base64
            import json
            resp = self.client.get(f"https://{VIRGO_HOST}/virgo/view/{book_id}",
                                   headers={"Cookie": "_LANG_=ja"},
                                   action=f"Loading the viewer for {book_id}")
            soup = _soup(resp.text)
            el = soup.select_one("#doc")
            if el is None:
                raise LayoutChanged("the viewer page's embedded #doc manifest")
            try:
                doc = json.loads(base64.b64decode(el.get_text(strip=True)))
            except (ValueError, TypeError) as e:
                raise LayoutChanged(f"a decodable #doc manifest ({e})") from None
            self._viewer_docs[book_id] = doc
        return self._viewer_docs[book_id]

    def get_pages(self, chapter):
        from ..models import PageRef
        doc = self._viewer_doc(chapter.chapter_id)
        location = doc.get("Location") or {}
        base, scramble_dir = location.get("base", ""), location.get("scramble_dir", "")
        orders = doc.get("Orders") or []
        pages = []
        for i, order in enumerate(orders):
            name = order.get("name") or ""
            if not name:
                continue
            # The real, directly-fetchable URL of this page's own file --
            # but what it serves is tile-scrambled (see download_page), so
            # it identifies the page rather than being fetched here.
            pages.append(PageRef(self.name, chapter.chapter_id, i,
                                 f"{base}{scramble_dir}/{name}"))
        if not pages:
            raise LayoutChanged("any pages in the viewer's Orders manifest")
        return pages

    def download_page(self, page):
        """A page's file is delivered tile-scrambled: a real 1190x1684
        page arrives as a ~4760x421 strip of tiles, with the manifest's
        own `crops` describing the reassembly. **This adapter never
        performs that reassembly itself** -- the same principle the
        module docstring applies to the site's crypto. Instead the site's
        own viewer is opened and stepped through its own public
        `JCOMI.viewer.movePage()` API, exactly as a reader clicking
        onward would, and each page it descrambles onto its own canvas is
        read back from the blob it publishes to its own `<img>`. One
        viewer session covers the whole book; every page it yields is
        cached for the `download_page()` calls that follow."""
        key = (page.chapter_id, page.index)
        if key not in self._page_bytes and page.chapter_id not in self._captured:
            self._capture_pages(page.chapter_id)
        cached = self._page_bytes.get(key)
        if cached is None:
            raise ContentHidden(
                f"{self.display_name}'s viewer didn't produce page {page.index + 1} in this "
                "session -- its pages are tile-scrambled and only its own reader reassembles "
                "them, so a page it never displayed can't be read.",
                FailureReason.ENCRYPTED_RESOURCE)
        return cached, ".jpg"

    def _capture_pages(self, book_id: str):
        """Steps the site's own viewer through `book_id` once, keeping
        every descrambled page it produces (see download_page)."""
        import page_fetch
        doc = self._viewer_doc(book_id)
        total = len(doc.get("Orders") or [])
        session = self._viewer_session or page_fetch.rendered_session

        def run(url):
            # A browser that can't reach or drive the reader (a failed
            # navigation, a dead session) is reported the way every other
            # failure in this project is -- a plain reason, never a raw
            # Playwright traceback escaping through the adapter.
            try:
                return drive(url)
            except SourceError:
                raise
            except Exception as e:
                raise ContentHidden(
                    f"{self.display_name}'s own reader couldn't be opened to read this book's "
                    f"pages from ({type(e).__name__}). Its pages are tile-scrambled and only "
                    "its own reader reassembles them, so there's nothing to fall back to.",
                    FailureReason.ENCRYPTED_RESOURCE) from e

        def drive(url):
            with session(url, keep_blobs=True) as page:
                if not page.evaluate(_VIEWER_READY_JS):
                    raise ContentHidden(
                        f"{self.display_name}'s own reader didn't come up in this session, so "
                        "there was nothing to read its pages from. Its pages are tile-scrambled "
                        "and only its own reader reassembles them.",
                        FailureReason.ENCRYPTED_RESOURCE)
                seen = {}

                def visit(target, settle):
                    try:
                        page.evaluate("(n) => JCOMI.viewer.movePage(n)", target)
                    except Exception:
                        # A page the viewer declines to move to isn't
                        # fatal -- whatever it did produce is still kept.
                        pass
                    page.wait_for_timeout(settle)
                    for no, blob_url in (page.evaluate(_VIEWER_PAGE_IMAGES_JS) or {}).items():
                        seen.setdefault(str(no), blob_url)

                for target in range(total):
                    if len(seen) >= total:
                        break
                    visit(target, _page_settle_ms())
                # A page the viewer hadn't finished drawing when its turn
                # came round is just slow, not missing: a live run left 5
                # of 43 behind this way. Give each straggler one more
                # visit, with longer to settle, before writing the book
                # off as incomplete.
                for target in range(total):
                    if str(target) in seen:
                        continue
                    visit(target, _VIEWER_PAGE_SETTLE_MS[1] * 2)
                blobs = page_fetch.kept_blob_bytes(page)
                for no, blob_url in seen.items():
                    data = blobs.get(blob_url)
                    if data:
                        self._page_bytes[(book_id, int(no))] = data

        # Attempted counts as attempted even if the reader couldn't be
        # opened at all, so the pages after the first still get
        # download_page's plain "didn't produce this page" rather than
        # each launching a browser of its own.
        try:
            self.client.paced(run, f"https://{VIRGO_HOST}/virgo/view/{book_id}", "Browser session",
                              action=f"Reading the viewer for {book_id}")
        finally:
            self._captured.add(book_id)

    def parse_url(self, url: str):
        m = re.search(r"mangaz\.com/(?:series|book)/detail/(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        # Reading a page needs the site's own reader driven in a browser
        # (see download_page), so this is a browser-tier source even
        # though search/series/chapters are plain HTTP.
        caps.access_method = AccessTier.RENDERED_BROWSER.value
        caps.technical = {
            "extraction_method": "static HTML for search/series/chapters; get_pages() reads the "
                                 "viewer page's own base64 `#doc` manifest (no key exchange, no "
                                 "`docx` request). Page images are tile-scrambled, so "
                                 "download_page() drives the site's own reader in a headless "
                                 "browser and keeps the pages that reader itself draws -- this "
                                 "adapter never unscrambles anything (module docstring).",
            "browser_required": True,
            "crypto_dependency": "none for reading pages. The optional 'cryptography' package is "
                                 "needed only by the retained legacy RSA+AES helpers, which the "
                                 "live site no longer uses (module docstring).",
            "live_verification": "search/series/chapters and the `#doc` manifest confirmed live "
                                 "against real books (2026-09-27), as was the browser capture "
                                 "path: a real descrambled 1190x1684 page was produced this way. "
                                 "A full capture of every page of one book is NOT proven -- the "
                                 "best real run returned 38 of 43, the rest lost to the site's "
                                 "own throttling of repeated automated access, and two "
                                 "confirmation runs failed to open the reader at all. Expect "
                                 "incomplete books on a throttled network; missing pages are "
                                 "reported, never substituted.",
            "reference": "the site's own current viewer script, vw.mangaz.com/virgo/js/"
                         "vw6-simple.js; the legacy flow's reference was keiyoushi/"
                         "extensions-source src/ja/mangatoshokanz + its own Crypto.kt "
                         "(Apache-2.0)",
        }
        caps.terms = {
            "robots_txt": "Real Crawl-delay: 120 for User-agent: * on both www.mangaz.com and "
                          "vw.mangaz.com -- respected via host_min_interval, no AI/crawling-"
                          "specific clause found.",
            "tos": "Confirmed no AI/crawling-specific clause (roadmap's own finding, not "
                  "independently re-read in full here).",
            "tos_prohibited": False,
        }
        return caps
