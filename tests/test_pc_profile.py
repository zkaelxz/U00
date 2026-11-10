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
    pcp.set_pin("123456")
    pcp.reset_state()          # a server restart forgets who was unlocked
    assert pcp.status() == {"pin_set": True, "guest": True}
    assert _titles(LOCAL) == {"shown"}
    assert _titles(None) == {"shown"}


def test_unlock_shows_everything_and_lock_hides_again():
    _make_two_titles()
    pcp.set_pin("123456")
    pcp.lock()
    assert _titles(LOCAL) == {"shown"}
    pcp.unlock("123456")
    assert _titles(LOCAL) == {"shown", "hidden"}
    pcp.lock()
    assert _titles(LOCAL) == {"shown"}


def test_guest_cannot_open_or_flip_a_hidden_title():
    _, hidden = _make_two_titles()
    pcp.set_pin("123456")
    pcp.lock()
    assert not own.can_see(LOCAL, "drama", hidden)
    with pytest.raises(Exception) as err:
        own.set_private(LOCAL, "drama", hidden, False)
    assert "not found" in str(err.value).lower()


def test_wrong_pin_is_refused_and_five_misses_pause_attempts():
    pcp.set_pin("123456")
    pcp.lock()
    for _ in range(pcp.MAX_FAILURES):
        with pytest.raises(ForbiddenError):
            pcp.unlock("000000")
    with pytest.raises(RateLimitedError):
        pcp.unlock("123456")          # even the right PIN waits out the pause
    assert pcp.status()["guest"] is True


def test_changing_the_pin_needs_the_current_one_and_not_while_guest():
    pcp.set_pin("123456")
    with pytest.raises(ForbiddenError):
        pcp.set_pin("567890", "000000")
    pcp.set_pin("567890", "123456")
    pcp.lock()
    with pytest.raises(ForbiddenError):
        pcp.set_pin("999999", "567890")
    with pytest.raises(ForbiddenError):
        pcp.unlock("123456")
    pcp.unlock("567890")


@pytest.mark.parametrize("pin", ["", "12345", "abcdef", "12 345", "1" * 13, "١٢٣٤٥٦", None, 123456])
def test_pin_format(pin):
    with pytest.raises(InvalidInputError):
        pcp.set_pin(pin)


def test_clearing_the_pin_needs_it_and_turns_profiles_off():
    _make_two_titles()
    pcp.set_pin("123456")
    pcp.lock()
    with pytest.raises(ForbiddenError):
        pcp.clear_pin("000000")
    pcp.clear_pin("123456")
    assert pcp.status() == {"pin_set": False, "guest": False}
    assert _titles(LOCAL) == {"shown", "hidden"}


def test_the_pin_is_stored_hashed():
    pcp.set_pin("123456")
    assert "123456" not in str(db.get_app_setting(pcp.PIN_KEY))


def test_routes(client):
    assert client.get("/api/pc-profile").json() == {"pin_set": False, "guest": False}
    r = client.post("/api/pc-profile/pin", json={"new_pin": "432100"})
    assert r.status_code == 200 and r.json() == {"pin_set": True, "guest": False, "hidden": 0}
    assert client.post("/api/pc-profile/lock", headers={"X-Baihe-Local": "1"}).json()["guest"] is True
    assert client.post("/api/pc-profile/unlock", json={"pin": "000000"}).status_code == 403
    assert client.post("/api/pc-profile/unlock", json={"pin": "432100"}).json()["guest"] is False
    assert client.post("/api/pc-profile/clear", json={"pin": "432100"}).json()["pin_set"] is False


def test_turning_on_can_hide_every_title_made_so_far():
    series = db.get_or_create_series("Saga")
    in_series = db.create_drama(title_zh="in series", series_id=series)
    alone = db.create_drama(title_zh="alone")
    member = db.auth_create_user("m@example.com")
    theirs = db.create_drama(title_zh="member's", owner_user_id=member)
    result = pcp.set_pin("123456", hide_existing=True)
    assert result["hidden"] == 2           # the series and the lone title
    pcp.lock()
    assert _titles(LOCAL) == {"member's"}
    assert not own.can_see(LOCAL, "drama", in_series) and not own.can_see(LOCAL, "drama", alone)
    assert own.can_see(LOCAL, "drama", theirs)


def test_without_the_option_nothing_is_marked_private():
    db.create_drama(title_zh="alone")
    assert pcp.set_pin("123456")["hidden"] == 0


def test_guest_narrows_only_what_a_request_lists_or_reads():
    """The owner's tools keep working on a private title while Guest is up."""
    _, hidden = _make_two_titles()
    pcp.set_pin("123456")
    pcp.lock()
    assert not own.can_see(LOCAL, "drama", hidden)
    own.require_editable(LOCAL, "drama", hidden)        # extension Save page, bulk work
    own.require_drama_work_cancellable(LOCAL, hidden)   # stop a bulk-translate batch
    assert own.sees_learned_style(LOCAL, {"learned_by": 7})
    assert own.may_cancel_job(LOCAL, "translate_%d" % hidden, None)


def test_wrong_pins_pause_attempts_across_a_restart():
    pcp.set_pin("123456")
    pcp.lock()
    for _ in range(pcp.MAX_FAILURES):
        with pytest.raises(ForbiddenError):
            pcp.unlock("000000")
    pcp.reset_state()          # a restart forgets memory, not the stored pause
    with pytest.raises(RateLimitedError):
        pcp.unlock("123456")


def test_a_damaged_stored_pin_never_matches_and_can_be_cleared():
    db.set_app_setting(pcp.PIN_KEY, "garbage-without-a-separator")
    pcp.reset_state()
    with pytest.raises(ForbiddenError):
        pcp.unlock("123456")
    pcp.clear_pin(None, forgot=True)
    assert pcp.status() == {"pin_set": False, "guest": False}


def test_setting_hiding_and_clearing_are_audited():
    db.create_drama(title_zh="alone")
    pcp.set_pin("123456", hide_existing=True)
    pcp.clear_pin("123456")
    actions = [a["action"] for a in db.auth_list_audit(50)]
    assert {"pc_profile.set_pin", "pc_profile.hide_existing", "pc_profile.clear_pin"} <= set(actions)


def test_a_restore_keeps_this_pcs_pin():
    from services import library_restore_service as lrs
    assert pcp.PIN_KEY in lrs._RESTORE_KEPT_APP_SETTINGS
    assert pcp.BLOCK_KEY in lrs._RESTORE_KEPT_APP_SETTINGS


def test_forgetting_the_cache_rereads_the_pin():
    pcp.set_pin("123456")
    db.set_app_setting(pcp.PIN_KEY, None)    # what a restore from a pre-PIN library leaves
    pcp.forget_cached_pin()
    assert pcp.status() == {"pin_set": False, "guest": False}


def test_routes_are_refused_when_sign_in_is_on(isolated_db):
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
    assert TestClient(app, raise_server_exceptions=False).get("/api/pc-profile").status_code in (401, 403, 404)


def test_guest_changes_nothing_for_signed_in_people_on_the_household_listener():
    """Remote access on: the PC window is Guest, accounts signed in from away are not."""
    _make_two_titles()
    admin = db.auth_create_user("admin@example.com", is_admin=True)
    member = db.auth_create_user("m@example.com")
    away_admin = {"user_id": admin, "is_admin": True, "is_local_owner": False, "admin_override": False}
    away_member = {"user_id": member, "is_admin": False, "is_local_owner": False, "admin_override": False}
    pcp.set_pin("123456")
    pcp.lock()
    assert _titles(LOCAL) == {"shown"}
    assert _titles(away_admin) == {"shown", "hidden"}
    assert _titles(away_member) == {"shown"}
