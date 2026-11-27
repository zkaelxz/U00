"""Following a Grab page's series: the series is tracked with its current chapters known, so the first check announces nothing old."""

from types import SimpleNamespace

import pytest

from lib.errors import InvalidInputError
from services import sources_tracking_service as svc
from sources import registry, store


class _Adapter:
    name = "fakesite"
    allow_browser = True

    def __init__(self, parsed, fail=None):
        self._parsed, self._fail = parsed, fail

    def parse_url(self, url):
        return self._parsed

    def capabilities(self):
        return None

    def supports(self, method):
        return True

    def get_series(self, series_id):
        if self._fail:
            raise self._fail
        return SimpleNamespace(title="Night Owl", url="https://fake.example/night-owl?x=1")

    def get_chapters(self, series_id):
        return [SimpleNamespace(chapter_id="1", title="One", group="", url=""),
                SimpleNamespace(chapter_id="2", title="Two", group="", url="")]


@pytest.fixture
def site(isolated_db, monkeypatch):
    adapter = _Adapter(("series", "night-owl"))
    monkeypatch.setattr(registry, "adapter_class_for_url", lambda url: _Adapter)
    monkeypatch.setattr(registry, "get_adapter", lambda name, **kw: adapter)
    monkeypatch.setattr(svc.search, "enabled_source", lambda name: _Adapter)
    monkeypatch.setattr(svc.search.ladder, "check_terms", lambda name, caps: None)
    monkeypatch.setattr(svc.chapter_order, "reading_order", lambda a, chapters: chapters)
    return adapter


def test_following_tracks_the_series_with_its_chapters_known(site):
    out = svc.follow_from_page("https://fake.example/night-owl/2")
    assert out == {"title": "Night Owl", "already": False}
    assert site.allow_browser is False
    row, = store.list_tracked_series()
    assert (row["source"], row["series_id"], row["title"]) == ("fakesite", "night-owl", "Night Owl")
    assert "?" not in row["url"]
    assert {c["chapter_id"] for c in store.known_chapters("fakesite", "night-owl")} == {"1", "2"}


def test_following_twice_changes_nothing(site):
    svc.follow_from_page("https://fake.example/night-owl")
    assert svc.follow_from_page("https://fake.example/night-owl") == {"title": "Night Owl", "already": True}
    assert len(store.list_tracked_series()) == 1


def test_a_site_no_source_matches_is_refused(isolated_db, monkeypatch):
    monkeypatch.setattr(registry, "adapter_class_for_url", lambda url: None)
    with pytest.raises(InvalidInputError):
        svc.follow_from_page("https://nowhere.example/x")
    assert store.list_tracked_series() == []


def test_a_page_that_is_not_a_series_is_refused(site):
    site._parsed = None
    with pytest.raises(InvalidInputError):
        svc.follow_from_page("https://fake.example/")
    assert store.list_tracked_series() == []


def test_a_site_error_tracks_nothing(site):
    site._fail = RuntimeError("blocked")
    with pytest.raises(InvalidInputError):
        svc.follow_from_page("https://fake.example/night-owl")
    assert store.list_tracked_series() == []


def test_a_follow_is_refused_while_another_runs(site):
    from lib.errors import ConflictError
    assert svc._follow_lock.acquire(blocking=False)
    try:
        with pytest.raises(ConflictError):
            svc.follow_from_page("https://fake.example/night-owl")
    finally:
        svc._follow_lock.release()
    assert store.list_tracked_series() == []


def test_an_oversized_or_control_character_address_is_refused(site):
    for bad in ("https://fake.example/" + "a" * 2100, "https://fake.example/a\nb"):
        with pytest.raises(InvalidInputError):
            svc.follow_from_page(bad)


def test_a_series_tracked_during_the_fetch_is_left_alone(site, monkeypatch):
    def slow_chapters(series_id):
        store.track_series("fakesite", "night-owl", "Mine", "", 7)
        return []
    monkeypatch.setattr(site, "get_chapters", slow_chapters)
    assert svc.follow_from_page("https://fake.example/night-owl") == {"title": "Mine", "already": True}
    assert store.list_tracked_series()[0]["drama_id"] == 7


def test_a_chapter_link_follows_its_series(site):
    site._parsed = ("chapter", SimpleNamespace(series_id="night-owl"))
    assert svc.follow_from_page("https://fake.example/night-owl/3")["already"] is False


def test_a_member_cannot_follow_more_series_than_the_limit(site, monkeypatch):
    from lib.errors import ConflictError
    from services import sources_registry_service
    monkeypatch.setattr(sources_registry_service, "MEMBER_TRACKED_LIMIT", 1)
    store.track_series("fakesite", "other", "Other", "", None, linked_by_user_id=7)
    with pytest.raises(ConflictError) as err:
        svc.follow_from_page("https://fake.example/night-owl", principal={"user_id": 7})
    assert "already tracking 1 series" in err.value.message
    # The owner (no account) is never counted.
    assert svc.follow_from_page("https://fake.example/night-owl")["already"] is False


def test_following_a_series_someone_else_tracks_makes_the_caller_a_follower(site):
    store.track_series("fakesite", "night-owl", "Night Owl", "", None, linked_by_user_id=8)
    assert svc.follow_from_page("https://fake.example/night-owl",
                                principal={"user_id": 7}) == {"title": "Night Owl", "already": True}
    assert store.followers()[("fakesite", "night-owl")] == {7, 8}
