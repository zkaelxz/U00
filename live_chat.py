"""Reads a YouTube live chat for a Live session and translates it in batches, newest first."""
import json
import re
import threading
from collections import deque
from urllib.parse import parse_qs, urlsplit

import background_jobs
from lib import http
from lib.errors import InvalidInputError

CHAT_PAGE = "https://www.youtube.com/live_chat"
CHAT_API = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
# A page that only needs to look like a browser's: YouTube answers a bare client with a consent wall.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.8", "Cookie": "SOCS=CAI"}
TIMEOUT = (10, 20)
MAX_PAGE_BYTES = 3_000_000
MAX_REPLY_BYTES = 2_000_000

MAX_KEPT = 300
MAX_TEXT_CHARS = 200
MAX_AUTHOR_CHARS = 60
# Spending, not reading, is what these bound: a busy chat can send hundreds of lines a minute.
BATCH_SIZE = 15
MAX_BACKLOG = 40
MAX_TRANSLATED = 1500
MIN_POLL_SECONDS = 2.0
MAX_POLL_SECONDS = 8.0
FAILURES_BEFORE_GIVING_UP = 5

PENDING, DONE, FAILED, SKIPPED = "pending", "done", "failed", "skipped"

NEEDS_YOUTUBE = "Chat needs a YouTube video or live link."
NO_CHAT = "This stream has no live chat to read."
CONNECTING = "Connecting to the chat…"
READING = "Reading the chat."
LOST = "The chat stopped answering, so it is no longer being read."
ENDED = "The chat has ended."
SESSION_ENDED = "The session ended, so the chat is no longer being read."
CAP_REACHED = "Chat translation limit reached for this session; new messages are shown as written."

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                  "youtube-nocookie.com", "www.youtube-nocookie.com"}
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SHORTCODE = re.compile(r":[A-Za-z0-9_\-]{1,40}:")
_SCRIPTS = {
    "ja": re.compile(r"[぀-ヿ㐀-鿿]"),
    "zh": re.compile(r"[㐀-鿿]"),
    "ko": re.compile(r"[가-힯㐀-鿿]"),
}


def video_id(url: str):
    """The 11-character video id of a YouTube watch, live, shorts or youtu.be link, else None."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    candidate = None
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        segments = [s for s in parts.path.split("/") if s]
        if segments and segments[0] in ("live", "shorts", "embed", "v") and len(segments) > 1:
            candidate = segments[1]
        elif segments and segments[0] == "watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
    return candidate if candidate and _ID.match(candidate) else None


def require_video_id(url: str) -> str:
    vid = video_id(url)
    if vid is None:
        raise InvalidInputError(NEEDS_YOUTUBE)
    return vid


def _text(value) -> str:
    """Plain text of a YouTube text object. A custom emote has no text of its own and is dropped
    (the :shortcode: it carries is the channel's own name for it, not words)."""
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"]
    out = []
    for run in value.get("runs") or ():
        if isinstance(run, dict) and isinstance(run.get("text"), str):
            out.append(run["text"])
        elif isinstance(run, dict) and isinstance(run.get("emoji"), dict):
            emoji = run["emoji"]
            if not emoji.get("isCustomEmoji") and isinstance(emoji.get("emojiId"), str):
                out.append(emoji["emojiId"])
    return "".join(out)


def clean_text(text: str) -> str:
    """One line of chat with links, channel emote codes and control characters removed: the text
    is typed by strangers and is shown and sent to a translator as data."""
    text = _URL.sub("[link]", text)
    text = _SHORTCODE.sub(" ", _CONTROL.sub(" ", text))
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT_CHARS]


def parse_page(html: str):
    """(api_key, client_version, continuation) from the chat page, or None when it holds no chat."""
    key = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
    version = (re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
               or re.search(r'"clientVersion"\s*:\s*"([^"]+)"', html))
    start = re.search(r"ytInitialData\"?\]?\s*=\s*", html)
    if not (key and version and start):
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start.end():])
    except ValueError:
        return None
    renderer = ((data.get("contents") or {}).get("liveChatRenderer")) if isinstance(data, dict) else None
    token = _continuation_of((renderer or {}).get("continuations"))
    if not token:
        return None
    return key.group(1), version.group(1), token[0]


def _continuation_of(continuations):
    """(token, wait_ms) from a continuations list, else None."""
    for entry in continuations or ():
        if not isinstance(entry, dict):
            continue
        for body in entry.values():
            if isinstance(body, dict) and isinstance(body.get("continuation"), str):
                timeout = body.get("timeoutMs")
                return body["continuation"], timeout if isinstance(timeout, int) else 0
    return None


def parse_reply(reply):
    """([{id, author, text, paid}], next (token, wait_ms) or None) from one chat poll."""
    live = ((reply.get("continuationContents") or {}).get("liveChatContinuation")
            if isinstance(reply, dict) else None)
    if not isinstance(live, dict):
        return [], None
    messages = []
    for action in live.get("actions") or ():
        item = ((action or {}).get("addChatItemAction") or {}).get("item") or {}
        for kind in ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer"):
            body = item.get(kind)
            if not isinstance(body, dict):
                continue
            text = clean_text(_text(body.get("message")))
            paid = clean_text(_text(body.get("purchaseAmountText"))) if kind.startswith("liveChatPaid") else ""
            if text or paid:
                messages.append({"id": str(body.get("id") or ""),
                                 "author": clean_text(_text(body.get("authorName")))[:MAX_AUTHOR_CHARS],
                                 "text": text, "paid": paid})
    return messages, _continuation_of(live.get("continuations"))


def worth_translating(text: str, source_language: str, recent: "deque") -> bool:
    """False for what a translator can add nothing to: no source-language characters (an emoji row,
    numbers, English), one character repeated, a link, or a line another chatter just sent."""
    stripped = text.replace("[link]", "").strip()
    script = _SCRIPTS.get(source_language)
    if not stripped or (script and not script.search(stripped)):
        return False
    if len(stripped) >= 3 and len(set(stripped)) <= 2:
        return False
    key = stripped.casefold()
    if key in recent:
        return False
    recent.append(key)
    return True


class ChatBoard:
    """The kept messages of one session, newest last, shared by the reader and the status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._messages = deque(maxlen=MAX_KEPT)
        self._next_id = 1
        self.state = "connecting"
        self.message = CONNECTING

    def add(self, author: str, text: str, paid: str, state: str) -> dict:
        with self._lock:
            entry = {"id": self._next_id, "author": author, "text": text, "paid": paid,
                     "translation": "", "state": state}
            self._next_id += 1
            self._messages.append(entry)
            return entry

    def finish(self, entry: dict, translation: str, state: str) -> None:
        with self._lock:
            entry["translation"], entry["state"] = translation, state

    def set_state(self, state: str, message: str) -> None:
        with self._lock:
            self.state, self.message = state, message

    def close(self, message: str) -> None:
        """Ends a still-live state; a reason already given (no chat, lost, ended) is kept."""
        with self._lock:
            if self.state in ("connecting", "reading"):
                self.state, self.message = "ended", message

    def snapshot(self, after: int = 0) -> dict:
        """Messages with id above `after`. A pending one changes in place, so a client passes the id
        before its oldest pending message."""
        with self._lock:
            return {"state": self.state, "message": self.message,
                    "messages": [dict(m) for m in self._messages if m["id"] > after],
                    "next_id": self._next_id - 1}


class NoStreakGuard:
    """The cue translator's spend guard with outcomes kept out of its streak: a bad chat request must
    not end the captions, but every request still honours the monthly cap and is logged."""

    def __init__(self, guard):
        self._guard = guard

    def before(self):
        self._guard.before()

    def after(self, error=None, usable=True):
        # Never a success either: that would reset the cue requests' failure streak.
        self._guard.after(None, False)


class ChatWorker:
    """One thread per session: poll the chat, keep what arrives, translate the newest worthwhile lines."""

    def __init__(self, session_id: str, vid: str, board: ChatBoard, translator, source_language: str,
                 report_note=None, sleep=None):
        self.session_id, self.vid, self.board = session_id, vid, board
        self.translator, self.source_language = translator, source_language
        self._note = report_note or (lambda text, key=None: None)
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._recent = deque(maxlen=60)
        self._seen = deque(maxlen=500)
        self._translated = 0
        self._thread = None

    def start(self) -> None:
        # Context variables do not cross threads: without this a member's own hosted-model key is lost
        # and the chat would be billed to the PC owner's account.
        from engine_backends.local import carry_member_cloud_key, member_cloud_key
        self._thread = threading.Thread(target=carry_member_cloud_key(self._run, member_cloud_key()),
                                        name=f"live-chat-{self.session_id}", daemon=True)
        self._thread.start()

    def stop(self, join_seconds: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_seconds)
        # The status is all the panel has: without this it says "Reading the chat" after the session ended.
        self.board.close(SESSION_ENDED)

    def _done(self) -> bool:
        return self._stop.is_set() or background_jobs.is_cancel_requested(self.session_id)

    def _run(self) -> None:
        try:
            self._read()
        except background_jobs.JobCancelled:
            pass
        except Exception:
            self.board.set_state("lost", LOST)
            self._note(LOST, key="chat")

    def _read(self) -> None:
        try:
            page = self._fetch_page()
        except http.FetchError:
            page = None
        if page is None:
            self.board.set_state("unavailable", NO_CHAT)
            self._note(NO_CHAT, key="chat")
            return
        key, version, token = page
        wait_ms, failures = 0, 0
        self.board.set_state("reading", READING)
        while not self._done():
            try:
                fresh, following = self._poll(key, version, token)
            except http.FetchError:
                failures += 1
                if failures >= FAILURES_BEFORE_GIVING_UP:
                    self.board.set_state("lost", LOST)
                    self._note(LOST, key="chat")
                    return
                self._sleep(MAX_POLL_SECONDS)
                continue
            failures = 0
            if following is None:
                self.board.set_state("ended", ENDED)
                return
            token, wait_ms = following
            self._take(fresh)
            self._sleep(min(MAX_POLL_SECONDS, max(MIN_POLL_SECONDS, wait_ms / 1000.0)))

    def _fetch_page(self):
        reply = http.get(CHAT_PAGE, params={"is_popout": "1", "v": self.vid}, headers=_HEADERS,
                         timeout=TIMEOUT, max_bytes=MAX_PAGE_BYTES, guard=None, allow_redirects=False)
        return parse_page(reply.text()) if reply.status == 200 else None

    def _poll(self, key, version, token):
        # The key is the public one every YouTube page embeds for the web client, not a secret.
        body = {"context": {"client": {"clientName": "WEB", "clientVersion": version}},
                "continuation": token}
        reply = http.post(CHAT_API, params={"key": key}, json=body, headers=_HEADERS, timeout=TIMEOUT,
                          max_bytes=MAX_REPLY_BYTES, guard=None, allow_redirects=False)
        if reply.status != 200:
            raise http.FetchError()
        try:
            return parse_reply(json.loads(reply.text()))
        except ValueError:
            raise http.FetchError() from None

    def _take(self, fresh: list) -> None:
        todo = []
        for message in fresh:
            if message["id"] and message["id"] in self._seen:
                continue
            self._seen.append(message["id"])
            capped = self._translated + len(todo) >= MAX_TRANSLATED
            wanted = (not capped and message["text"]
                      and worth_translating(message["text"], self.source_language, self._recent))
            entry = self.board.add(message["author"], message["text"], message["paid"],
                                   PENDING if wanted else SKIPPED)
            if wanted:
                todo.append(entry)
            elif capped and message["text"]:
                self._note(CAP_REACHED, key="chat-cap")
        # Behind by more than a few batches the old lines are stale; the newest are the ones worth the spend.
        for entry in todo[:-MAX_BACKLOG]:
            self.board.finish(entry, "", SKIPPED)
        todo = todo[-MAX_BACKLOG:]
        for start in range(0, len(todo), BATCH_SIZE):
            if self._done():
                return
            self._translate(todo[start:start + BATCH_SIZE])

    def _translate(self, entries: list) -> None:
        try:
            found = self.translator.translate_chunk([e["text"] for e in entries])
        except background_jobs.JobCancelled:
            raise
        except Exception:
            found = {}
        self._translated += len(entries)
        for pos, entry in enumerate(entries):
            text = found.get(pos)
            self.board.finish(entry, text or "", DONE if text else FAILED)
