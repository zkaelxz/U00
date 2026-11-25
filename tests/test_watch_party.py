"""Watch parties: the room store (owner isolation, caps, validation) and its routes."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import InvalidInputError, NotFoundError, RateLimitedError
from services import watch_party_service as party
from tests.api_helpers import loopback_client

ME = {"user_id": 1}
OTHER = {"user_id": 2}


@pytest.fixture(autouse=True)
def _clean_rooms():
    party._rooms.clear()
    party._streams.clear()
    yield
    party._rooms.clear()
    party._streams.clear()


@pytest.fixture
def drama(isolated_db, _see_every_title):
    return db.create_drama(title_en="T", source_language="zh")


@pytest.fixture
def _see_every_title(monkeypatch):
    monkeypatch.setattr(party.ownership_service, "require_drama", lambda drama_id: {"id": drama_id})


def test_a_room_starts_paused_at_zero(drama):
    room = party.create_room(ME, drama)
    assert (room["playing"], room["position"], room["drama_id"]) == (False, 0.0, drama)


def test_another_account_gets_not_found_like_a_code_that_never_existed(drama):
    code = party.create_room(ME, drama)["code"]
    for call in (lambda: party.get_room(OTHER, code), lambda: party.set_state(OTHER, code, True, 5.0),
                 lambda: party.end_room(OTHER, code), lambda: party.open_stream(OTHER, code)):
        with pytest.raises(NotFoundError):
            call()
    with pytest.raises(NotFoundError):
        party.get_room(ME, "nosuchcode")


def test_a_play_state_moves_the_seq_and_is_validated(drama):
    code = party.create_room(ME, drama)["code"]
    first = party.get_room(ME, code)["seq"]
    out = party.set_state(ME, code, True, 12.5)
    assert out["seq"] > first and out["playing"] and out["position"] == 12.5
    for playing, position in ((1, 5.0), (True, -1), (True, float("nan")), (True, float("inf")),
                              (True, 10 ** 9), (True, True), (True, "5")):
        with pytest.raises(InvalidInputError):
            party.set_state(ME, code, playing, position)


def test_rooms_per_account_are_capped_and_an_idle_one_is_dropped(drama, monkeypatch):
    for _ in range(party.MAX_ROOMS_PER_PRINCIPAL):
        party.create_room(ME, drama)
    with pytest.raises(RateLimitedError):
        party.create_room(ME, drama)
    party.create_room(OTHER, drama)
    monkeypatch.setattr(party, "_now", lambda: 10 ** 10)
    assert party.create_room(ME, drama)["seq"] == 1


def test_streams_are_counted_and_capped_per_account(drama):
    code = party.create_room(ME, drama)["code"]
    held = [party.open_stream(ME, code)[0] for _ in range(party.MAX_STREAMS_PER_PRINCIPAL)]
    assert party.get_room(ME, code)["watching"] == party.MAX_STREAMS_PER_PRINCIPAL
    with pytest.raises(RateLimitedError):
        party.open_stream(ME, code)
    party.close_stream(held[0])
    assert party.get_room(ME, code)["watching"] == party.MAX_STREAMS_PER_PRINCIPAL - 1


def test_an_ended_room_reads_as_gone_to_its_listeners(drama):
    code = party.create_room(ME, drama)["code"]
    party.end_room(ME, code)
    assert party.poll(ME, code, 0) is None


def test_the_routes_create_sync_and_end(isolated_db):
    did = db.create_drama(title_en="T", source_language="zh")
    client = loopback_client(create_app(ApiSettings()))
    made = client.post("/api/watch-party/rooms", json={"drama_id": did})
    assert made.status_code == 200
    code = made.json()["code"]
    assert client.post(f"/api/watch-party/rooms/{code}/state", json={"playing": True, "position": 3}).json()["playing"]
    assert client.get(f"/api/watch-party/rooms/{code}").json()["position"] == 3
    assert client.post(f"/api/watch-party/rooms/{code}/state", json={"playing": "yes", "position": 3}).status_code == 422
    assert client.post(f"/api/watch-party/rooms/{code}/state", json={"playing": True, "position": -2}).status_code == 422
    assert client.delete(f"/api/watch-party/rooms/{code}").json() == {"ended": True}
    assert client.get(f"/api/watch-party/rooms/{code}").status_code == 404
    assert client.get("/api/watch-party/rooms/bad!code!x").status_code == 422
    assert client.post("/api/watch-party/rooms", json={"drama_id": 99999}).status_code == 404


def test_the_stream_sends_the_state_then_tells_listeners_the_party_ended(drama, monkeypatch):
    import asyncio
    from api.routers import watch_party_routes as routes
    monkeypatch.setattr(routes, "POLL_SECONDS", 0.01)

    class Here:
        async def is_disconnected(self):
            return False

    async def run():
        stream, first = party.open_stream(ME, party.create_room(ME, drama)["code"])
        code = first["code"]
        frames = []
        async for frame in routes._stream(Here(), ME, code, stream, first, lambda: ME):
            frames.append(frame)
            if len(frames) == 1:
                party.set_state(ME, code, True, 9.0)
            elif len(frames) == 3:  # retry hint, first state, the play
                party.end_room(ME, code)
        return frames, stream
    frames, stream = asyncio.run(run())
    kinds = [f.split("\n")[0] for f in frames]
    assert kinds == ["retry: 3000", "event: state", "event: state", "event: ended"]
    assert '"playing":true' in frames[2]
    assert stream not in party._streams


def test_a_title_the_caller_cannot_see_reads_as_missing(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Private", source_language="zh")
    monkeypatch.setattr(party.ownership_service, "can_see", lambda principal, kind, item_id: False)
    with pytest.raises(NotFoundError) as hidden:
        party.create_room(ME, did)
    with pytest.raises(NotFoundError) as missing:
        party.create_room(ME, did + 1000)
    assert str(hidden.value) == str(missing.value)
