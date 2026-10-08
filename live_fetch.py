"""
live_fetch.py -- fetches a live stream in Python and writes its bytes to
ffmpeg's stdin, so live capture's ffmpeg (run with `-protocol_whitelist
pipe`) never opens a URL itself: whatever a playlist, manifest or redirect
names is either fetched here, through the caller's proxy (the app passes a
services/egress_proxy.GuardedProxy, which checks every hop), or not at all.

An HLS stream (a body starting `#EXTM3U`) is followed here: a master
playlist's lowest-bandwidth variant (or its audio rendition), the media
playlist polled for new segments by media sequence number, AES-128 keys
(decrypted here, with the `cryptography` package) and fMP4 init sections.
Anything else is one http(s) response piped as it arrives. Refused with a
fixed message: SAMPLE-AES and other key formats, byte-range segments.

Own code rather than yt-dlp's downloader: its native HLS downloader does
not take live streams and hands them to ffmpeg, which is what this avoids.
"""
import contextlib
import queue
import re
import socket
import threading
import time
import weakref
from urllib.parse import urljoin, urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
# No new segment (HLS), or ffmpeg taking no bytes, for this long ends the
# capture; a live playlist that keeps failing to refresh, or one playlist,
# key or segment request still unfinished after it, ends the same way.
STALL_TIMEOUT = 60.0
CHUNK_BYTES = 65_536
# The fetcher is at most this many chunks ahead of ffmpeg (backpressure
# instead of buffering a stream that ffmpeg can't keep up with).
QUEUE_CHUNKS = 32
MAX_PLAYLIST_BYTES = 1_000_000
# A live window holds a few dozen entries; this bounds the work one
# hostile playlist can cause on every refresh.
MAX_PLAYLIST_ENTRIES = 2000
MAX_SEGMENT_BYTES = 64_000_000
MAX_REDIRECTS = 5
# Where a live playlist starts playing from, as ffmpeg's HLS demuxer does.
LIVE_EDGE_SEGMENTS = 3
# requests' `verify`: True (its CA bundle) or a CA bundle path.
TLS_VERIFY = True
_POLL = 0.2

# Every message below is fixed text: it reaches the job record and the
# client, and must never carry a stream URL or the proxy secret.
UNREACHABLE = "The stream could not be reached."
REFUSED = "The stream address was refused (not a public address) or could not be reached."
NOT_HTTP = "The stream named an address that is not http(s), so it was not opened."
STALLED = "The stream stopped sending audio."
FFMPEG_STALLED = "ffmpeg stopped reading the stream."
TOO_LARGE = "The stream sent a playlist or segment that is too large."
UNSUPPORTED = "The stream uses an HLS feature live capture doesn't support (byte ranges, " \
              "SAMPLE-AES or another key format)."
NEEDS_CRYPTOGRAPHY = ("The stream is encrypted (AES-128); decrypting it needs the cryptography "
                      "package: pip install cryptography")
FAILED = "Fetching the stream failed."


class StreamFetchError(Exception):
    """Ends the capture; str() is one of the fixed messages above."""


class _FetchFailed(StreamFetchError):
    """One request failed; an HLS segment, key or refresh is retried or
    skipped, the way ffmpeg's HLS demuxer treats them."""


class _Stopped(Exception):
    pass


_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(text: str) -> dict:
    return {m.group(1): m.group(2).strip('"') for m in _ATTR_RE.finditer(text)}


def _iv(value):
    if value is None:
        return None
    if not re.fullmatch(r"0[xX][0-9a-fA-F]{32}", value):
        raise StreamFetchError(UNSUPPORTED)
    return bytes.fromhex(value[2:])


def parse_playlist(text: str, base_url: str) -> dict:
    """{variants, audio, segments, target, endlist} of an m3u8 playlist.
    A segment is {seq, uri, key, init}; key is None or {uri, iv} (iv None
    means "the media sequence number"), init is None or {uri, key}."""
    lines = [ln.strip() for ln in text.lstrip("\ufeff \t\r\n").splitlines()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise StreamFetchError(FAILED)
    variants, audio, segments = [], {}, []
    first_seq, target, endlist, entries = 0, 6.0, False, 0
    key = init = variant = None
    for line in lines[1:]:
        tag, _, value = line.partition(":")
        if not line:
            continue
        if tag == "#EXT-X-MEDIA" or not line.startswith("#"):
            entries += 1
            if entries > MAX_PLAYLIST_ENTRIES:
                raise StreamFetchError(TOO_LARGE)
        if tag == "#EXT-X-STREAM-INF":
            variant = _attrs(value)
        elif tag == "#EXT-X-MEDIA":
            a = _attrs(value)
            if a.get("TYPE") == "AUDIO" and a.get("URI"):
                audio.setdefault(a.get("GROUP-ID", ""), []).append(
                    {**a, "URI": urljoin(base_url, a["URI"])})
        elif tag == "#EXT-X-MEDIA-SEQUENCE" and value.strip().isdigit():
            first_seq = int(value)
        elif tag == "#EXT-X-TARGETDURATION":
            try:
                target = float(value)
            except ValueError:
                pass
        elif tag == "#EXT-X-ENDLIST":
            endlist = True
        elif tag == "#EXT-X-BYTERANGE":
            raise StreamFetchError(UNSUPPORTED)
        elif tag == "#EXT-X-KEY":
            a = _attrs(value)
            method = a.get("METHOD", "").upper()
            if method == "NONE":
                key = None
            elif (method == "AES-128" and a.get("URI")
                  and a.get("KEYFORMAT", "identity") == "identity"):
                key = {"uri": urljoin(base_url, a["URI"]), "iv": _iv(a.get("IV"))}
            else:
                raise StreamFetchError(UNSUPPORTED)
        elif tag == "#EXT-X-MAP":
            a = _attrs(value)
            if "BYTERANGE" in a or not a.get("URI"):
                raise StreamFetchError(UNSUPPORTED)
            init = {"uri": urljoin(base_url, a["URI"]), "key": key}
        elif line.startswith("#"):
            continue
        elif variant is not None:
            variants.append({**variant, "uri": urljoin(base_url, line)})
            variant = None
        else:
            segments.append({"seq": first_seq + len(segments), "uri": urljoin(base_url, line),
                             "key": key, "init": init})
    return {"variants": variants, "audio": audio, "segments": segments,
            "target": min(max(target, 1.0), 30.0), "endlist": endlist}


def pick_rendition(master: dict) -> str:
    """The lowest-bandwidth variant (only its audio is used), or that
    variant's audio rendition when it names a separate one."""
    def bandwidth(v):
        value = v.get("BANDWIDTH", "")
        return int(value) if value.isdigit() else 0
    variant = min(master["variants"], key=bandwidth)
    group = master["audio"].get(variant.get("AUDIO", "")) if variant.get("AUDIO") else None
    if group:
        default = [a for a in group if a.get("DEFAULT", "").upper() == "YES"]
        return (default or group)[0]["URI"]
    return variant["uri"]


def _decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise StreamFetchError(NEEDS_CRYPTOGRAPHY) from None
    if len(data) % 16:
        raise _FetchFailed(FAILED)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(decryptor.update(data) + decryptor.finalize()) + unpadder.finalize()
    except ValueError:
        raise _FetchFailed(FAILED) from None


def _tracking_pool(pool_cls, track, release):
    base = pool_cls.ConnectionCls

    class Connection(base):
        _handshake_handle = None

        def _new_conn(self):
            sock = super()._new_conn()
            # TLS moves the descriptor into a new socket object and empties
            # this one, so a duplicate keeps the connection reachable by
            # halt() through the proxy's CONNECT answer and the handshake.
            self._handshake_handle = sock.dup()
            track(self._handshake_handle)
            return sock

        def connect(self):
            try:
                super().connect()
            finally:
                if self._handshake_handle is not None:
                    release(self._handshake_handle)
                    self._handshake_handle = None
            track(self.sock)

    return type(pool_cls.__name__, (pool_cls,), {"ConnectionCls": Connection})


class _TrackingAdapter(HTTPAdapter):
    """Hands every socket its connections open to track (and a temporary
    one to release once done with it), so halt() can wake a request at
    any stage: the TLS handshake, waiting for response headers (no
    response object exists yet) or reading the body."""

    def __init__(self, track, release):
        self._pool_classes = {
            "http": _tracking_pool(HTTPConnectionPool, track, release),
            "https": _tracking_pool(HTTPSConnectionPool, track, release)}
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = self._pool_classes

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        manager.pool_classes_by_scheme = self._pool_classes
        return manager


def _shutdown(sock):
    # The plain socket.socket method: SSLSocket.shutdown also drops its
    # TLS state, which the thread reading from it may be using.
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, TypeError):
        pass


class StreamPump:
    """Fetches `url` on one thread and writes the bytes to `sink` (ffmpeg's
    stdin) on another, through a queue of at most QUEUE_CHUNKS chunks.
    `proxy`: every request goes through it (http and https alike); None
    connects directly. Environment proxy settings are ignored, so no
    `no_proxy` entry can route a request around `proxy`.

    The writer closes `sink` when the stream ends, fails or is stopped,
    so ffmpeg sees end of input. `error` is the first failure (a fixed
    message), None after a clean end or a halt()."""

    def __init__(self, url: str, sink, proxy: str = None, stall_timeout: float = None):
        self.url = url
        self.error = None
        self._sink = sink
        self._stall_timeout = STALL_TIMEOUT if stall_timeout is None else stall_timeout
        self._queue = queue.Queue(maxsize=QUEUE_CHUNKS)
        self._stop = threading.Event()
        self._fetch_done = threading.Event()
        self._lock = threading.Lock()
        self._sockets = weakref.WeakSet()
        # Which _deadline a timer belongs to, so one that fires late never
        # shuts down the sockets of the request after it.
        self._deadline_generation = 0
        self._deadline_expired = False
        self._threads = []
        self._session = requests.Session()
        adapter = _TrackingAdapter(self._track, self._release)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._session.trust_env = False
        self._session.verify = TLS_VERIFY
        self._session.proxies = {"http": proxy, "https": proxy} if proxy else {}

    def start(self) -> "StreamPump":
        for target, name in ((self._fetch_main, "live-fetch"), (self._write_main, "live-pipe")):
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            self._threads.append(thread)
        return self

    def halt(self):
        """Ask both threads to stop without waiting. A blocked fetch is
        woken by shutting its sockets down; a write blocked on a full pipe
        ends when ffmpeg exits."""
        self._stop.set()
        with self._lock:
            for sock in list(self._sockets):
                _shutdown(sock)

    # Shutdowns and _release's close share the lock, so halt() never shuts
    # down a handshake handle's descriptor number after its close freed it.
    def _track(self, sock):
        with self._lock:
            self._sockets.add(sock)
            if self._stop.is_set() or self._deadline_expired:  # opened after the shutdowns
                _shutdown(sock)

    def _release(self, sock):
        with self._lock:
            self._sockets.discard(sock)
            sock.close()

    def join(self, timeout: float = READ_TIMEOUT + 5):
        for thread in self._threads:
            thread.join(timeout)
        self._session.close()

    def alive(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def _fail(self, message: str):
        if self.error is None and not self._stop.is_set():
            self.error = message
        self._stop.set()

    def _check_stop(self):
        if self._stop.is_set():
            raise _Stopped

    # --- writer -------------------------------------------------------

    def _write_main(self):
        try:
            while not self._stop.is_set():
                try:
                    data = self._queue.get(timeout=_POLL)
                except queue.Empty:
                    if self._fetch_done.is_set() and self._queue.empty():
                        return
                    continue
                self._sink.write(data)
                self._sink.flush()
        except (OSError, ValueError):
            self._fail(FFMPEG_STALLED)
        finally:
            try:
                self._sink.close()
            except (OSError, ValueError):
                pass

    # --- fetcher ------------------------------------------------------

    def _fetch_main(self):
        try:
            self._fetch(self.url)
        except _Stopped:
            pass
        except StreamFetchError as exc:
            self._fail(str(exc))
        except Exception:  # noqa: BLE001 -- the text could name a URL; never shown
            self._fail(FAILED)
        finally:
            self._fetch_done.set()

    def _emit(self, data: bytes):
        for start in range(0, len(data), CHUNK_BYTES):
            piece = data[start:start + CHUNK_BYTES]
            deadline = time.monotonic() + self._stall_timeout
            while True:
                self._check_stop()
                try:
                    self._queue.put(piece, timeout=_POLL)
                    break
                except queue.Full:
                    if time.monotonic() >= deadline:
                        raise StreamFetchError(FFMPEG_STALLED) from None

    @contextlib.contextmanager
    def _deadline(self, deadline: float):
        """Ends the request made inside it as STALLED once `deadline`
        passes; yields a function that disarms the timer early. Read
        timeouts are per recv and the proxy's head deadline does not see
        inside a CONNECT tunnel, so without this an https host trickling
        its handshake or headers, or a body's chunk-size and trailer
        lines (http.client reads those inside one read1 call), would hold
        the request open indefinitely."""
        with self._lock:
            self._deadline_generation += 1
            self._deadline_expired = False
            generation = self._deadline_generation
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._expire,
                                (generation,))
        timer.daemon = True
        timer.start()
        try:
            yield lambda: self._disarm(timer)
        except _FetchFailed:
            if self._disarm(timer):
                raise StreamFetchError(STALLED) from None
            raise
        except BaseException:
            self._disarm(timer)
            raise
        # A body cut short by the timer's shutdown can read as a clean end.
        if self._disarm(timer):
            raise StreamFetchError(STALLED)

    def _expire(self, generation: int):
        with self._lock:
            if generation != self._deadline_generation:
                return
            self._deadline_expired = True
            for sock in list(self._sockets):
                _shutdown(sock)

    def _disarm(self, timer) -> bool:
        """Whether the deadline passed; afterwards the timer can no
        longer shut anything down."""
        timer.cancel()
        with self._lock:
            self._deadline_generation += 1
            expired, self._deadline_expired = self._deadline_expired, False
        return expired

    def _follow(self, url: str):
        """A streamed 200 response for url, following at most
        MAX_REDIRECTS http(s) redirects, each one a request of its own
        through the proxy."""
        session = self._session
        for _ in range(MAX_REDIRECTS + 1):
            self._check_stop()
            if urlsplit(url).scheme not in ("http", "https"):
                raise StreamFetchError(NOT_HTTP)
            try:
                response = session.get(url, stream=True, allow_redirects=False,
                                       headers={"Accept-Encoding": "identity"},
                                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except requests.exceptions.ProxyError:
                raise _FetchFailed(REFUSED) from None
            except requests.exceptions.Timeout:
                raise _FetchFailed(STALLED) from None
            except (requests.exceptions.RequestException, ValueError):
                raise _FetchFailed(UNREACHABLE) from None
            if response.is_redirect:
                location = response.headers.get("Location", "")
                response.close()
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                status = response.status_code
                response.close()
                raise _FetchFailed(REFUSED if status in (403, 407, 502)
                                   else f"The stream server answered HTTP {status}.")
            # Bytes are passed on undecoded, so a compressed body would
            # reach ffmpeg (or the playlist parser) as noise.
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                response.close()
                raise _FetchFailed(FAILED)
            return response
        raise StreamFetchError(UNREACHABLE)

    def _chunks(self, response):
        """Whatever has arrived, up to CHUNK_BYTES at a time (read1), so a
        slow stream reaches ffmpeg as it comes, not a full chunk later."""
        try:
            while True:
                self._check_stop()
                chunk = response.raw.read1(CHUNK_BYTES, decode_content=False)
                if not chunk:
                    return
                yield chunk
        except (urllib3.exceptions.HTTPError, requests.exceptions.RequestException, OSError):
            self._check_stop()
            raise _FetchFailed(STALLED) from None

    def _read(self, url: str, limit: int) -> tuple:
        """(body, final url) of one GET bounded in size and in time."""
        with self._deadline(time.monotonic() + self._stall_timeout):
            response = self._follow(url)
            try:
                body = bytearray()
                for chunk in self._chunks(response):
                    body += chunk
                    if len(body) > limit:
                        raise StreamFetchError(TOO_LARGE)
                return bytes(body), response.url
            finally:
                response.close()

    def _fetch(self, url: str):
        with self._deadline(time.monotonic() + self._stall_timeout) as disarm:
            response = self._follow(url)
            try:
                chunks = self._chunks(response)
                first = next(chunks, b"")
                content_type = response.headers.get("Content-Type", "").lower()
                if not (first.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#EXTM3U")
                        or "mpegurl" in content_type):
                    # A stream that is not HLS has no end; only its start is bounded.
                    if disarm():
                        raise StreamFetchError(STALLED)
                    self._emit(first)
                    for chunk in chunks:
                        self._emit(chunk)
                    return
                body = bytearray(first)
                for chunk in chunks:
                    body += chunk
                    if len(body) > MAX_PLAYLIST_BYTES:
                        raise StreamFetchError(TOO_LARGE)
                final_url = response.url
            finally:
                response.close()
        self._hls(bytes(body).decode("utf-8", "replace"), final_url)

    def _playlist(self, url: str) -> tuple:
        body, final_url = self._read(url, MAX_PLAYLIST_BYTES)
        return parse_playlist(body.decode("utf-8", "replace"), final_url), final_url

    def _key(self, uri: str, keys: dict) -> bytes:
        if uri not in keys:
            key, _ = self._read(uri, 16)
            if len(key) != 16:
                raise _FetchFailed(FAILED)
            if len(keys) >= 4:
                keys.pop(next(iter(keys)))
            keys[uri] = key
        return keys[uri]

    def _hls(self, text: str, url: str):
        playlist = parse_playlist(text, url)
        if playlist["variants"]:
            playlist, url = self._playlist(pick_rendition(playlist))
            if playlist["variants"]:
                raise StreamFetchError(UNSUPPORTED)
        next_seq, init_uri, keys = None, None, {}
        last_new = time.monotonic()
        while True:
            segments = playlist["segments"]
            if segments and next_seq is not None \
                    and segments[-1]["seq"] < next_seq - 1 - len(segments):
                next_seq = None  # the sequence restarted (a new broadcast): rejoin at the edge
            if segments and next_seq is None:
                start = 0 if playlist["endlist"] else max(0, len(segments) - LIVE_EDGE_SEGMENTS)
                next_seq = segments[start]["seq"]
            new = [s for s in segments if next_seq is not None and s["seq"] >= next_seq]
            for segment in new:
                if self._segment(segment, keys, init_uri):
                    init_uri = segment["init"]["uri"] if segment["init"] else None
                    last_new = time.monotonic()
                next_seq = segment["seq"] + 1
            if playlist["endlist"]:
                return
            if time.monotonic() - last_new > self._stall_timeout:
                raise StreamFetchError(STALLED)
            # RFC 8216 6.3.4: reload after a target duration, or half of
            # one when the last reload brought nothing new.
            if self._stop.wait(playlist["target"] if new else playlist["target"] / 2):
                raise _Stopped
            try:
                playlist, url = self._playlist(url)
            except _FetchFailed:
                pass

    def _segment(self, segment: dict, keys: dict, init_uri) -> bool:
        """Emits one segment (after its init section, when that changed);
        False when a request failed and the segment was skipped, as
        ffmpeg skips one."""
        try:
            init = segment["init"]
            if init and init["uri"] != init_uri:
                init_key = init["key"]
                if init_key and init_key["iv"] is None:
                    raise StreamFetchError(UNSUPPORTED)
                key_bytes = self._key(init_key["uri"], keys) if init_key else None
                data, _ = self._read(init["uri"], MAX_SEGMENT_BYTES)
                if init_key:
                    data = _decrypt(data, key_bytes, init_key["iv"])
                self._emit(data)
            key = segment["key"]
            key_bytes = self._key(key["uri"], keys) if key else None
            data, _ = self._read(segment["uri"], MAX_SEGMENT_BYTES)
            if key:
                data = _decrypt(data, key_bytes, key["iv"] or segment["seq"].to_bytes(16, "big"))
            self._emit(data)
        except _FetchFailed:
            return False
        return True
