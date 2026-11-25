"""Grab's pick-up-where-you-left-off: one last page per account per site, kept on the PC."""

import pytest

from lib.errors import InvalidInputError
from services import grab_last_page_service as svc


def test_a_page_is_remembered_per_account_and_site(isolated_db):
    svc.remember(1, "https://a.example/owl/3?x=1#frag", "Night Owl  ch 3")
    svc.remember(1, "https://a.example/owl/4", "Night Owl ch 4")
    svc.remember(1, "https://b.example/x", "")
    svc.remember(2, "https://a.example/other", "Someone else")
    mine = svc.places(1)["pages"]
    assert {p["host"]: p["url"] for p in mine} == {"a.example": "https://a.example/owl/4", "b.example": "https://b.example/x"}
    assert mine[0]["host"] == "b.example"
    assert svc.places(2)["pages"][0]["url"] == "https://a.example/other"
    assert svc.places(3)["pages"] == []


def test_only_scheme_host_and_path_are_kept(isolated_db):
    svc.remember(1, "https://user:pw@A.example:8443/read?token=SECRET#top", "T")
    assert svc.places(1)["pages"][0]["url"] == "https://a.example:8443/read"


@pytest.mark.parametrize("bad", ["", None, 5, "javascript:alert(1)", "file:///etc/passwd", "https:///x", "http://a.example/p",
                                 "https://192.168.1.5/x", "https://localhost/x", "https://a.example:99999/",
                                 "https://[::1",
                                 "https://a.example/" + "x" * 600, "https://a.example/a\nb"])
def test_what_is_not_a_web_page_is_refused(isolated_db, bad):
    with pytest.raises(InvalidInputError):
        svc.remember(1, bad, "t")
    assert svc.places(1)["pages"] == []


def test_the_list_per_account_is_capped(isolated_db):
    for i in range(130):
        svc.remember(1, f"https://s{i}.example/p", "t")
    assert len(svc.places(1)["pages"]) == 100


def test_a_deleted_member_takes_their_places_along(isolated_db):
    from sources import store_last_page
    svc.remember(5, "https://a.example/p", "t")
    svc.remember(6, "https://a.example/q", "t")
    store_last_page.forget(5)
    assert svc.places(5)["pages"] == [] and len(svc.places(6)["pages"]) == 1
