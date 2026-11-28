"""services/recap_service.py and GET /api/library/recap: one account's month in the Library."""
import datetime

import pytest

import db
from lib import clock
from services import auth_service, recap_service


def _at(monkeypatch, *when):
    monkeypatch.setattr(clock, "utcnow", lambda: datetime.datetime(*when))


def _member(email):
    uid = auth_service.add_user(email)["id"]
    return uid, {"user_id": uid, "is_admin": False, "is_local_owner": False,
                 "permissions": sorted(auth_service.effective_permissions(uid))}


def _read(monkeypatch, did, uid, when, percent=10.0):
    _at(monkeypatch, *when)
    db.save_progress(did, percent_complete=percent, user_id=uid)


def test_each_account_sees_only_its_own_month(isolated_db, monkeypatch):
    a, pa = _member("a@example.com")
    b, pb = _member("b@example.com")
    novel = db.create_drama(title_en="Shared novel", media_type="novel")
    comic = db.create_drama(title_en="Shared comic", media_type="manhua")
    _read(monkeypatch, novel, a, (2026, 10, 3, 9))
    _read(monkeypatch, novel, a, (2026, 10, 4, 9))
    _read(monkeypatch, comic, a, (2026, 10, 4, 10))
    db.set_chapter_marks(comic, ["c1", "c2"], read=True, user_id=a)
    db.add_watch_bookmark(novel, a, 12.0, "", limit=50)
    _read(monkeypatch, comic, b, (2026, 10, 20, 9))

    mine = recap_service.get_recap("2026-10", principal=pa)
    assert (mine["titles_read"], mine["chapters_read"], mine["moments_saved"], mine["active_days"]) == (2, 2, 1, 2)
    assert mine["most_days"] == {"drama_id": novel, "title": "Shared novel", "days": 2}
    theirs = recap_service.get_recap("2026-10", principal=pb)
    assert (theirs["titles_read"], theirs["chapters_read"], theirs["moments_saved"], theirs["active_days"]) == (1, 0, 0, 1)
    assert theirs["most_days"]["drama_id"] == comic


def test_a_title_the_account_can_no_longer_see_is_neither_counted_nor_named(isolated_db, monkeypatch):
    a, pa = _member("a@example.com")
    b, _ = _member("b@example.com")
    secret = db.create_drama(title_en="Secret", media_type="manhua", owner_user_id=b)
    _read(monkeypatch, secret, a, (2026, 10, 5, 9))
    db.set_chapter_marks(secret, ["c1"], read=True, user_id=a)
    db.add_watch_bookmark(secret, a, 3.0, "", limit=50)
    db.update_drama(secret, is_private=1)

    out = recap_service.get_recap("2026-10", principal=pa)
    assert (out["titles_read"], out["chapters_read"], out["moments_saved"], out["active_days"]) == (0, 0, 0, 0)
    assert out["most_days"] is None and "Secret" not in str(out)


def test_the_month_runs_from_its_first_instant_to_the_next_months_first(isolated_db, monkeypatch):
    a, pa = _member("a@example.com")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _read(monkeypatch, did, a, (2026, 9, 30, 23, 59, 59))
    _read(monkeypatch, did, a, (2026, 10, 1, 0, 0, 0))
    _read(monkeypatch, did, a, (2026, 10, 31, 23, 59, 59))
    _read(monkeypatch, did, a, (2026, 11, 1, 0, 0, 0))
    assert recap_service.get_recap("2026-10", principal=pa)["active_days"] == 2
    assert recap_service.get_recap("2026-09", principal=pa)["active_days"] == 1
    assert recap_service.get_recap("2026-12", principal=pa)["titles_read"] == 0


def test_the_default_is_last_month_and_december_rolls_into_january(isolated_db, monkeypatch):
    _at(monkeypatch, 2026, 1, 3)
    assert recap_service.get_recap(principal=None)["month"] == "2025-12"
    a, pa = _member("a@example.com")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _read(monkeypatch, did, a, (2025, 12, 31, 22))
    assert recap_service.get_recap("2025-12", principal=pa)["titles_read"] == 1


def test_a_title_counts_as_finished_only_the_first_month_it_reaches_the_end(isolated_db, monkeypatch):
    a, pa = _member("a@example.com")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _read(monkeypatch, did, a, (2026, 9, 10), percent=100.0)
    _read(monkeypatch, did, a, (2026, 10, 10), percent=100.0)
    assert recap_service.get_recap("2026-09", principal=pa)["titles_finished"] == 1
    assert recap_service.get_recap("2026-10", principal=pa)["titles_finished"] == 0


def test_the_pc_itself_has_its_own_month_apart_from_members(isolated_db, monkeypatch):
    a, pa = _member("a@example.com")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _read(monkeypatch, did, 0, (2026, 10, 2))
    db.set_chapter_marks(did, ["c1"], read=True, user_id=0)
    assert recap_service.get_recap("2026-10", principal=pa)["active_days"] == 0
    assert recap_service.get_recap("2026-10", principal=None)["titles_read"] == 1


def test_spend_counts_a_month_older_than_spend_historys_table(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Novel", media_type="novel")
    _at(monkeypatch, 2020, 3, 9)
    db.log_usage(did, "claude", "model-x", "translate", estimated_cost_usd=0.5)
    _at(monkeypatch, 2026, 11, 2)
    assert recap_service.get_recap("2020-03", principal=None)["spend_usd"] == pytest.approx(0.5)


def test_an_admin_away_from_the_pc_gets_no_spend_but_at_the_pc_does(isolated_db, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api import auth as api_auth
    from api.api_config import ApiSettings
    from api.server import create_app
    host = "baihe.example.com"
    settings = ApiSettings(household_port=8610, serve_frontend=False, google_client_id="cid",
                           google_client_secret="secret-value", public_url=f"https://{host}")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _at(monkeypatch, 2026, 10, 9)
    db.log_usage(did, "claude", "model-x", "translate", estimated_cost_usd=0.25)
    admin_id = auth_service.grant_admin_local("owner@example.com")["id"]
    s = auth_service.create_session(admin_id, "pytest", "127.0.0.1")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}

    def spend(app, base_url):
        c = TestClient(app, base_url=base_url, client=("127.0.0.1", 5000), raise_server_exceptions=False)
        r = c.get("/api/library/recap?month=2026-10", headers=h)
        assert r.status_code == 200, r.text
        return r.json()["spend_usd"]

    assert spend(create_app(settings, listener="household"), f"http://{host}") is None
    assert spend(create_app(settings), "http://127.0.0.1:8600") == pytest.approx(0.25)


def test_spend_is_only_for_a_caller_who_may_open_spend_history(isolated_db, monkeypatch):
    _, pa = _member("a@example.com")
    did = db.create_drama(title_en="Novel", media_type="novel")
    _at(monkeypatch, 2026, 10, 9)
    db.log_usage(did, "claude", "model-x", "translate", estimated_cost_usd=0.25)
    _at(monkeypatch, 2026, 11, 1)
    db.log_usage(did, "claude", "model-x", "translate", estimated_cost_usd=9.0)
    assert recap_service.get_recap("2026-10", principal=None)["spend_usd"] == pytest.approx(0.25)
    assert recap_service.get_recap("2026-10", principal=pa)["spend_usd"] is None
    assert recap_service.get_recap("2026-10", principal={**pa, "permissions": ["admin.settings"]})["spend_usd"] \
        == pytest.approx(0.25)


@pytest.mark.parametrize("month", ["2026-13", "26-10", "1999-12", "9999-12", "2026-1"])
def test_a_malformed_month_is_refused(isolated_db, month):
    from lib.errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        recap_service.get_recap(month)


class TestRoute:
    @pytest.fixture
    def away(self, isolated_db):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                          raise_server_exceptions=False)

    def _headers(self, uid):
        from api import auth as api_auth
        s = auth_service.create_session(uid)
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}

    def test_a_member_gets_their_month_without_the_pcs_spend(self, away, monkeypatch):
        uid, _ = _member("a@example.com")
        did = db.create_drama(title_en="Novel", media_type="novel")
        _read(monkeypatch, did, uid, (2026, 10, 2))
        db.log_usage(did, "claude", "model-x", "translate", estimated_cost_usd=1.0)
        r = away.get("/api/library/recap?month=2026-10", headers=self._headers(uid))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["titles_read"] == 1 and body["spend_usd"] is None

    def test_signing_in_and_library_read_are_needed(self, away):
        assert away.get("/api/library/recap").status_code == 401
        uid, _ = _member("a@example.com")
        auth_service.revoke_permission(uid, "library.read")
        assert away.get("/api/library/recap", headers=self._headers(uid)).status_code == 403

    def test_a_bad_month_is_422(self, away):
        uid, _ = _member("a@example.com")
        assert away.get("/api/library/recap?month=2026-13", headers=self._headers(uid)).status_code == 422
