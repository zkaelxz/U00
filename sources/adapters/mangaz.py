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

**Verified against the real, live site while building this adapter**:
`www.mangaz.com` and `vw.mangaz.com` both reachable; `vw.mangaz.com/virgo/
app.js` contains a real, current `__serial` value; a real ticket exchange
(HEAD to `virgo/view/<a real book id>`) returns a real `virgo!__ticket`
session cookie. **The full RSA+AES round trip against a real chapter's
real encrypted payload was not completed in this environment** -- this
session's own sandboxed-agent classifier stopped a further live request
to the paid-content decrypt endpoint as resembling an attack pattern
before a real ciphertext was ever obtained, and this adapter does not
attempt to route around that stop. Every other verifiable piece (protocol
sequence read directly from the real extension's real source, RSA-512
keygen, RSA/PKCS1v1.5, and AES-CBC/PKCS7 all exercised end-to-end against
a locally-generated key and a locally-encrypted payload) is real and
tested; only a live decrypt of this site's actual paid content is not --
recorded here honestly, per this step's own manual-check exit condition,
rather than claimed.

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

import re
import secrets
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SearchResult, \
    SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.mangaz.com"
VIRGO_HOST = "vw.mangaz.com"   # a fixed subdomain in the real site, not per-mirror (module docstring)


class LayoutChanged(SourceError):
    """The page/response didn't have what this adapter expects -- most
    likely the site changed its markup or protocol. Reported plainly,
    never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"mangaz's page/response has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


class CryptoUnavailable(SourceError):
    def __init__(self):
        super().__init__(
            "mangaz.com needs the optional 'cryptography' package for its session-scoped "
            "RSA+AES page decryption (see requirements.txt) -- install it to use this source.",
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
    if not manifest.get("Images") or not manifest.get("Location"):
        raise LayoutChanged("Images/Location in the decrypted page manifest")
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

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._private_key = None
        self._serial = None
        self._series_pages = {}

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

    # -- pages: the real RSA+AES flow (module docstring) ------------------
    def get_pages(self, chapter):
        from ..models import PageRef
        book_id = chapter.chapter_id
        private_key = self._keys()
        serial = self._fetch_serial()
        ticket = self._fetch_ticket(book_id)
        pem = _public_key_pem(private_key.public_key())
        url = f"https://{VIRGO_HOST}/virgo/docx/{book_id}.json"
        headers = {"X-Requested-With": "XMLHttpRequest",
                  "Cookie": f"_LANG_=ja; virgo!__ticket={ticket}"}
        data = {"__serial": serial, "__ticket": ticket, "pub": pem}
        resp = self.client.post(url, data=data, headers=headers, classify_body=False,
                                action=f"Requesting pages for {chapter.title}")
        import json
        try:
            encrypted = json.loads(resp.text)
        except ValueError as e:
            raise LayoutChanged(f"a JSON encrypted-page response ({e})") from None
        manifest = decrypt_page_manifest(private_key, encrypted)
        base = manifest["Location"].get("base", "")
        st = manifest["Location"].get("st", "")
        pages = []
        for i, image in enumerate(manifest["Images"]):
            file_name = (image.get("file") or "").split(".", 1)[0]
            if not file_name:
                continue
            pages.append(PageRef(self.name, chapter.chapter_id, i, f"{base}{st}{file_name}.jpg"))
        if not pages:
            raise LayoutChanged("any page images in the decrypted manifest")
        return pages

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        return resp.content, ".jpg"

    def parse_url(self, url: str):
        m = re.search(r"mangaz\.com/(?:series|book)/detail/(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML for search/series/chapters; get_pages() runs the "
                                 "site's own real session-scoped RSA+AES exchange (a fresh "
                                 "512-bit RSA keypair per session, ticket+serial exchange, "
                                 "RSA/PKCS1v1.5-wrapped AES key, AES-CBC/PKCS7 page manifest) -- "
                                 "ported exactly from the real reference extension, never "
                                 "approximated (module docstring).",
            "browser_required": False,
            "crypto_dependency": "the optional 'cryptography' package -- RSA-512 keygen uses "
                                 "this module's own textbook Miller-Rabin prime generation "
                                 "because mainstream libraries refuse to *generate* a key this "
                                 "small, but every actual RSA/AES operation runs through "
                                 "'cryptography' itself, never hand-rolled crypto (module "
                                 "docstring).",
            "live_verification": "protocol sequence, domains, real serial value and a real "
                                 "ticket exchange were all confirmed live while building this "
                                 "adapter; a full live decrypt of a real chapter's real "
                                 "encrypted payload was not completed -- this session's own "
                                 "sandboxed-agent safety classifier stopped a further live "
                                 "request to the paid-content decrypt endpoint before a real "
                                 "ciphertext was obtained (module docstring). Not routed around.",
            "reference": "keiyoushi/extensions-source src/ja/mangatoshokanz + its own Crypto.kt "
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
