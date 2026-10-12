"""Followed Live channels: channel input, ownership, limits and options
(services/live_follow_service.py). No network: DNS is faked, nothing is
checked (the poller's request_check only queues)."""
import json
import socket

import pytest

from services import live_follow_poller, live_follow_service as svc
from services.service_errors import (ConflictError, DependencyUnavailableError, ForbiddenError,
                                     InvalidInputError, NotFoundError, RateLimitedError)
from sources import live_follow_store

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.72.14", 443))]
OWNER = {"user_id": None, "is_admin": True, "is_local_owner": True, "admin_override": True}
PC_ADMIN = {"user_id": 1, "is_admin": True, "is_local_owner": False, "admin_override": True}
REMOTE_ADMIN = {"user_id": 2, "is_admin": True, "is_local_owner": False, "admin_override": False}
ALICE = {"user_id": 11, "is_admin": False, "is_local_owner": False}
BOB = {"user_id": 12, "is_admin": False, "is_local_owner": False}
CHANNEL_ID = "UC" + "a" * 22


class _Thread:
    def is_alive(self):
        return True


@pytest.fixture
def follows(isolated_db, monkeypatch):
    """No thread really starts here: the poller's own tests run them."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    live_follow_poller.reset_for_tests()
    spawned = []
    monkeypatch.setattr(live_follow_poller, "_spawn",
                        lambda target, name: spawned.append(name) or _Thread())
    monkeypatch.setattr(live_follow_poller, "_thread", None)
    monkeypatch.setattr(live_follow_poller, "_worker", None)
    yield spawned
    live_follow_poller.reset_for_tests()


@pytest.mark.parametrize("text, url, label", [
    ("@SomeStreamer", "https://www.youtube.com/@SomeStreamer/live", "@SomeStreamer"),
    ("https://www.youtube.com/@name", "https://www.youtube.com/@name/live", "@name"),
    ("https://youtube.com/@name/streams?x=1#t", "https://www.youtube.com/@name/live", "@name"),
    ("http://m.youtube.com/@name/live", "https://www.youtube.com/@name/live", "@name"),
    ("www.youtube.com/@name", "https://www.youtube.com/@name/live", "@name"),
    (f"https://www.youtube.com/channel/{CHANNEL_ID}/videos",
     f"https://www.youtube.com/channel/{CHANNEL_ID}/live", f"channel/{CHANNEL_ID}"),
    ("https://www.youtube.com/c/SomeName", "https://www.youtube.com/c/SomeName/live", "c/SomeName"),
    ("@白鹤频道", "https://www.youtube.com/@%E7%99%BD%E9%B9%A4%E9%A2%91%E9%81%93/live", "@白鹤频道"),
])
def test_channel_input_is_reduced_to_one_canonical_url(text, url, label):
    assert svc.normalize_channel(text) == (url, label)


@pytest.mark.parametrize("text", [
    "", "   ", "name", "@ab", "@" + "a" * 31, "@..", "@a b", "@<script>",
    "https://evil.example/@name", "https://youtube.com.evil.example/@name",
    "https://www.youtube.com.evil.example/@name", "https://evilyoutube.com/@name",
    "https://user:pw@www.youtube.com/@name", "https://evil.example@www.youtube.com/@name",
    "https://www.youtube.com:8443/@name", "https://www.youtube.com:443/@name",
    "https://www.youtube.com:bad/@name",
    "javascript:alert(1)//www.youtube.com/@name", "file:///www.youtube.com/@name",
    "ftp://www.youtube.com/@name", "//www.youtube.com/@name",
    "https://www.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/",
    "https://www.youtube.com/channel/UCshort", "https://www.youtube.com/c/..",
    "https://www.youtube.com\\@evil.example/@name",
    "https://www.youtube.com/@" + "x" * 300,
    None, 5,
])
def test_hostile_or_unknown_channel_input_is_refused(text):
    with pytest.raises(InvalidInputError):
        svc.normalize_channel(text)


def test_private_address_is_refused_by_the_url_guard(follows, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(InvalidInputError):
        svc.add_follow("@name", principal=ALICE)
    assert live_follow_store.list_all() == []


def test_unresolvable_host_is_a_503(follows, monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror("no")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(DependencyUnavailableError):
        svc.add_follow("@name", principal=ALICE)


def test_view_carries_no_url_and_nothing_is_checked_by_default(follows):
    v = svc.add_follow("https://www.youtube.com/@name/featured", principal=ALICE)
    assert v["channel"] == "@name" and v["mode"] == "notify" and v["mine"] is True
    assert v["auto_check"] is False and v["checking"] is False
    assert "http" not in json.dumps(v) and "youtube.com" not in json.dumps(v)
    # Manual by default: no check queued, no thread started.
    assert live_follow_poller._requested == [] and follows == []
    row = live_follow_store.get(v["id"])
    assert row["channel_url"] == "https://www.youtube.com/@name/live"
    assert row["owner_user_id"] == 11 and row["pc_owner"] == 0


def test_members_see_only_their_own_follows_and_others_are_404(follows):
    a = svc.add_follow("@alice_ch", principal=ALICE)
    b = svc.add_follow("@bob_ch", principal=BOB)
    assert [f["id"] for f in svc.list_follows(ALICE)["items"]] == [a["id"]]
    assert [f["id"] for f in svc.list_follows(BOB)["items"]] == [b["id"]]
    for call in (lambda: svc.update_follow(b["id"], options={"whisper_size": "tiny"},
                                           principal=ALICE),
                 lambda: svc.remove_follow(b["id"], principal=ALICE),
                 lambda: svc.check_now(b["id"], principal=ALICE),
                 lambda: svc.start_from_follow(b["id"], principal=ALICE),
                 lambda: svc.follow_engine(b["id"], principal=ALICE),
                 lambda: svc.remove_follow(999, principal=ALICE),
                 lambda: svc.remove_follow("x", principal=ALICE),
                 lambda: svc.remove_follow(2 ** 70, principal=ALICE)):
        with pytest.raises(NotFoundError):
            call()
    assert live_follow_store.get(b["id"]) is not None
    # The local owner and admins see everyone's (the existing ownership rules).
    assert len(svc.list_follows(OWNER)["items"]) == 2
    assert len(svc.list_follows(REMOTE_ADMIN)["items"]) == 2
    assert all(not f["mine"] for f in svc.list_follows(REMOTE_ADMIN)["items"])


def test_an_admin_away_from_the_pc_changes_only_their_own(follows):
    b = svc.add_follow("@bob_ch", principal=BOB)
    with pytest.raises(ForbiddenError):
        svc.remove_follow(b["id"], principal=REMOTE_ADMIN)
    assert svc.remove_follow(b["id"], principal=OWNER)["removed"] is True


def test_limits_five_per_member_twenty_in_total(follows):
    for i in range(svc.MAX_PER_MEMBER):
        svc.add_follow(f"@alice{i:03}", principal=ALICE)
    with pytest.raises(ConflictError):
        svc.add_follow("@alice999", principal=ALICE)
    for i in range(svc.MAX_TOTAL - svc.MAX_PER_MEMBER):
        svc.add_follow(f"@owner{i:03}", principal=OWNER)
    assert live_follow_store.count(any_owner=True) == svc.MAX_TOTAL
    with pytest.raises(ConflictError):
        svc.add_follow("@bob_ch", principal=BOB)
    with pytest.raises(ConflictError):
        svc.add_follow("@owner999", principal=OWNER)


def test_the_same_channel_twice_is_refused_for_one_user_only(follows):
    svc.add_follow("@name", principal=ALICE)
    with pytest.raises(ConflictError):
        svc.add_follow("https://www.youtube.com/@name/live", principal=ALICE)
    assert svc.add_follow("@name", principal=BOB)["channel"] == "@name"


def test_auto_start_is_the_pc_owners_only_and_only_on_their_own(follows):
    with pytest.raises(ForbiddenError):
        svc.add_follow("@name", mode="auto", principal=ALICE)
    with pytest.raises(ForbiddenError):
        svc.add_follow("@name", mode="auto", principal=REMOTE_ADMIN)
    a = svc.add_follow("@name", principal=ALICE)
    with pytest.raises(ForbiddenError):
        svc.update_follow(a["id"], mode="auto", principal=ALICE)
    # The owner may not turn a member's follow into an automatic start either.
    with pytest.raises(ForbiddenError):
        svc.update_follow(a["id"], mode="auto", principal=OWNER)
    assert live_follow_store.get(a["id"])["mode"] == "notify"
    mine = svc.add_follow("@owner_ch", mode="auto", auto_check=True, principal=OWNER)
    assert mine["mode"] == "auto" and live_follow_store.get(mine["id"])["pc_owner"] == 1
    at_pc = svc.add_follow("@admin_ch", principal=PC_ADMIN)
    assert svc.update_follow(at_pc["id"], mode="auto", auto_check=True,
                             principal=PC_ADMIN)["mode"] == "auto"
    assert svc.list_follows(OWNER)["can_auto_start"] is True
    assert svc.list_follows(ALICE)["can_auto_start"] is False
    assert svc.list_follows(REMOTE_ADMIN)["can_auto_start"] is False


def test_options_are_bounded_like_a_live_start(follows, isolated_db):
    v = svc.add_follow("@name", principal=OWNER, options={
        "source_language": "ja", "whisper_size": "tiny", "engine": "fake", "max_minutes": 999,
        "use_gpu": True})
    assert v["options"]["max_minutes"] == 240 and v["options"]["source_language"] == "ja"
    for bad in ({"source_language": "xx"}, {"whisper_size": "huge"}, {"engine": "nope"},
                {"model": "m" * 101}, {"use_gpu": "yes"}, {"glossary_drama_id": True},
                {"url": "https://x"}, ["x"]):
        with pytest.raises(InvalidInputError):
            svc.update_follow(v["id"], options=bad, principal=OWNER)


def test_a_glossary_title_the_member_cannot_see_is_404(follows, isolated_db):
    import db
    did = db.create_drama(title_zh="Private one", owner_user_id=12, is_private=1)
    with pytest.raises(NotFoundError):
        svc.add_follow("@name", principal=ALICE, options={"glossary_drama_id": did})
    v = svc.add_follow("@name", principal=BOB, options={"glossary_drama_id": did})
    assert v["options"]["glossary_drama_id"] == did
    db.delete_drama(did)
    # A deleted title drops out of the options; the rest are kept.
    assert svc.list_follows(BOB)["items"][0]["options"]["glossary_drama_id"] is None


def test_auto_start_needs_the_hourly_check(follows):
    with pytest.raises(InvalidInputError):
        svc.add_follow("@owner_ch", mode="auto", principal=OWNER)
    v = svc.add_follow("@owner_ch", principal=OWNER)
    with pytest.raises(InvalidInputError):
        svc.update_follow(v["id"], mode="auto", principal=OWNER)
    assert svc.update_follow(v["id"], mode="auto", auto_check=True, principal=OWNER)["mode"] == "auto"
    # Turning the hourly check off turns the automatic start back into Notify me.
    off = svc.update_follow(v["id"], auto_check=False, principal=OWNER)
    assert (off["auto_check"], off["mode"]) == (False, "notify")
    with pytest.raises(InvalidInputError):
        svc.update_follow(v["id"], mode="auto", auto_check=False, principal=OWNER)


def test_the_hourly_toggle_starts_the_scheduler(follows):
    v = svc.add_follow("@name", principal=ALICE)
    assert follows == []
    assert svc.update_follow(v["id"], auto_check=True, principal=ALICE)["auto_check"] is True
    assert follows == ["live-follow-poller"]
    svc.add_follow("@other", auto_check=True, principal=BOB)
    assert follows.count("live-follow-poller") >= 1


def test_check_now_queues_once_and_refuses_right_after_a_check(follows):
    v = svc.add_follow("@name", principal=ALICE)
    assert svc.check_now(v["id"], principal=ALICE) == {"id": v["id"], "queued": True}
    assert live_follow_poller._requested == [v["id"]] and follows == ["live-follow-checks"]
    # A second click while it is queued changes nothing.
    assert svc.check_now(v["id"], principal=ALICE)["queued"] is True
    assert live_follow_poller._requested == [v["id"]]
    assert svc.list_follows(ALICE)["items"][0]["checking"] is True
    live_follow_poller.reset_for_tests()
    live_follow_store.update(v["id"], last_checked_at=1000.0)
    with pytest.raises(RateLimitedError):
        svc.check_now(v["id"], principal=ALICE, now=1030.0)
    assert svc.check_now(v["id"], principal=ALICE, now=1061.0)["queued"] is True


def test_check_all_queues_only_what_the_caller_may_change(follows):
    a1 = svc.add_follow("@alice1", principal=ALICE)["id"]
    a2 = svc.add_follow("@alice2", principal=ALICE)["id"]
    b = svc.add_follow("@bob_ch", principal=BOB)["id"]
    live_follow_store.update(a2, last_checked_at=1000.0)
    assert svc.check_all(ALICE, now=1030.0) == {"queued": 1, "skipped": 1}
    assert live_follow_poller._requested == [a1]
    assert svc.check_all(REMOTE_ADMIN, now=1030.0) == {"queued": 0, "skipped": 0}
    assert svc.check_all(OWNER, now=1030.0) == {"queued": 1, "skipped": 2}
    assert live_follow_poller._requested == [a1, b]


def test_remove_forgets_the_poller_state(follows):
    v = svc.add_follow("@name", principal=ALICE)
    live_follow_poller._due[v["id"]] = 1.0
    svc.remove_follow(v["id"], principal=ALICE)
    assert v["id"] not in live_follow_poller._due and v["id"] not in live_follow_poller._requested
    assert live_follow_store.get(v["id"]) is None


def test_start_from_follow_is_a_normal_live_start(follows, monkeypatch):
    from services import live_service
    seen = {}
    monkeypatch.setattr(live_service, "start_session",
                        lambda url, **kw: seen.update(url=url, **kw) or {"session_id": "live_x"})
    v = svc.add_follow("@name", principal=ALICE, options={"engine": "fake", "max_minutes": 5})
    assert svc.start_from_follow(v["id"], principal=ALICE) == {"session_id": "live_x"}
    assert seen["url"] == "https://www.youtube.com/@name/live"
    assert seen["principal"] is ALICE and seen["use_saved_cookies"] is False
    assert seen["engine"] == "fake" and seen["max_minutes"] == 5
    live_follow_store.update(v["id"], last_state="live", last_stream_id="abcdefghijk")
    svc.start_from_follow(v["id"], principal=ALICE)
    assert seen["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    # A stored id that isn't a video id is never put in a URL.
    live_follow_store.update(v["id"], last_stream_id="../../x?y=1")
    svc.start_from_follow(v["id"], principal=ALICE)
    assert seen["url"] == "https://www.youtube.com/@name/live"


def test_the_followed_channels_table_has_its_columns(follows):
    from sources import store
    with store.connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(followed_channels)")}
    assert {"id", "owner_user_id", "channel_url", "channel_name", "mode", "options",
            "poll_minutes", "last_checked_at", "last_status", "last_stream_id",
            "created_at"} <= cols
    with pytest.raises(ValueError):
        live_follow_store.update(1, channel_url="https://evil.example/")
