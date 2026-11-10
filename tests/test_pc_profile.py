"""PC window profiles: a PIN, the Guest default, and what Guest lists."""

import pytest

import db
from lib.errors import ForbiddenError, InvalidInputError, RateLimitedError
from services import ownership_service as own
from services import pc_profile_service as pcp

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


@pytest.fixture(autouse=True)
def _fresh_state(isolated_db):
    pcp.reset_state()
    yield
    pcp.reset_state()


def _titles(principal):
    return {d["title_zh"] for d in db.list_dramas(visible_to=own.visible_to_filter(principal))}


def _make_two_titles():
    shown = db.create_drama(title_zh="shown")
    hidden = db.create_drama(title_zh="hidden")
    assert db.set_item_private("drama", hidden, True)
    return shown, hidden


def test_without_a_pin_the_pc_sees_everything():
    _make_two_titles()
    assert pcp.status() == {"pin_set": False, "guest": False}
    assert _titles(LOCAL) == {"shown", "hidden"}
    assert _titles(None) == {"shown", "hidden"}


def test_with_a_pin_a_fresh_start_is_guest_and_hides_private_titles():
    _make_two_titles()
    pcp.set_pin("1234")
    pcp.reset_state()          # a server restart forgets who was unlocked
    assert pcp.status() == {"pin_set": True, "guest": True}
    assert _titles(LOCAL) == {"shown"}
    assert _titles(None) == {"shown"}


def test_unlock_shows_everything_and_lock_hides_again():
    _make_two_titles()
    pcp.set_pin("1234")
    pcp.lock()
    assert _titles(LOCAL) == {"shown"}
    pcp.unlock("1234")
    assert _titles(LOCAL) == {"shown", "hidden"}
    pcp.lock()
    assert _titles(LOCAL) == {"shown"}


def test_guest_cannot_open_or_flip_a_hidden_title():
    _, hidden = _make_two_titles()
    pcp.set_pin("1234")
    pcp.lock()
    assert not own.can_see(LOCAL, "drama", hidden)
    with pytest.raises(Exception) as err:
        own.set_private(LOCAL, "drama", hidden, False)
    assert "not found" in str(err.value).lower()


def test_wrong_pin_is_refused_and_five_misses_pause_attempts():
    pcp.set_pin("1234")
    pcp.lock()
    for _ in range(pcp.MAX_FAILURES):
        with pytest.raises(ForbiddenError):
            pcp.unlock("0000")
    with pytest.raises(RateLimitedError):
        pcp.unlock("1234")          # even the right PIN waits out the pause
    assert pcp.status()["guest"] is True


def test_changing_the_pin_needs_the_current_one_and_not_while_guest():
    pcp.set_pin("1234")
    with pytest.raises(ForbiddenError):
        pcp.set_pin("5678", "0000")
    pcp.set_pin("5678", "1234")
    pcp.lock()
    with pytest.raises(ForbiddenError):
        pcp.set_pin("9999", "5678")
    with pytest.raises(ForbiddenError):
        pcp.unlock("1234")
    pcp.unlock("5678")


@pytest.mark.parametrize("pin", ["", "123", "abcd", "12 34", "1" * 13, "١٢٣٤", None, 1234])
def test_pin_format(pin):
    with pytest.raises(InvalidInputError):
        pcp.set_pin(pin)


def test_clearing_the_pin_needs_it_and_turns_profiles_off():
    _make_two_titles()
    pcp.set_pin("1234")
    pcp.lock()
    with pytest.raises(ForbiddenError):
        pcp.clear_pin("0000")
    pcp.clear_pin("1234")
    assert pcp.status() == {"pin_set": False, "guest": False}
    assert _titles(LOCAL) == {"shown", "hidden"}


def test_the_pin_is_stored_hashed():
    pcp.set_pin("1234")
    assert "1234" not in str(db.get_app_setting(pcp.PIN_KEY))


def test_routes(client):
    assert client.get("/api/pc-profile").json() == {"pin_set": False, "guest": False}
    r = client.post("/api/pc-profile/pin", json={"new_pin": "4321"})
    assert r.status_code == 200 and r.json() == {"pin_set": True, "guest": False, "hidden": 0}
    assert client.post("/api/pc-profile/lock", headers={"X-Baihe-Local": "1"}).json()["guest"] is True
    assert client.post("/api/pc-profile/unlock", json={"pin": "0000"}).status_code == 403
    assert client.post("/api/pc-profile/unlock", json={"pin": "4321"}).json()["guest"] is False
    assert client.post("/api/pc-profile/clear", json={"pin": "4321"}).json()["pin_set"] is False


def test_turning_on_can_hide_every_title_made_so_far():
    series = db.get_or_create_series("Saga")
    in_series = db.create_drama(title_zh="in series", series_id=series)
    alone = db.create_drama(title_zh="alone")
    member = db.auth_create_user("m@example.com")
    theirs = db.create_drama(title_zh="member's", owner_user_id=member)
    result = pcp.set_pin("1234", hide_existing=True)
    assert result["hidden"] == 2           # the series and the lone title
    pcp.lock()
    assert _titles(LOCAL) == {"member's"}
    assert not own.can_see(LOCAL, "drama", in_series) and not own.can_see(LOCAL, "drama", alone)
    assert own.can_see(LOCAL, "drama", theirs)


def test_without_the_option_nothing_is_marked_private():
    db.create_drama(title_zh="alone")
    assert pcp.set_pin("1234")["hidden"] == 0
