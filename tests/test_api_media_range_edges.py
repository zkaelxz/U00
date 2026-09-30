"""Range / seek edge cases for the Slice 52 streaming routes (all `media.stream`):

  GET/HEAD /api/media/dramas/{id}/audio and /video
  GET      /api/dub/dramas/{id}/track
  GET      /api/artifacts/dramas/{id}/{kind}

Covers repo-work item 6 of docs/remote-access-decision.md ("verify Range and
seek behaviour") as far as it can be checked without a multi-GB real file:
the >4 GiB case uses a sparse file, so no real disk is used. Behaviour
is checked against RFC 9110 sections 13.1.5 (If-Range), 14 (Range requests)
and 15.5.17 (416), and against the Slice 52 paragraph in
docs/archive/migration-review.md. Any mismatch is a strict xfail with the root cause.

Complements tests/test_api_media_playback.py (the slice's own tests); cases
already asserted there are not repeated unless a new edge is added.
"""

import email.utils
import os
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app

DATA = bytes(range(256)) * 4  # 1024 bytes, every offset distinguishable in 256-byte blocks
SIZE = len(DATA)


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


@pytest.fixture
def did(isolated_db):
    """A drama with audio, source video, a dub track and one subtitle artifact."""
    import db
    d = db.create_drama(title_en="D")
    folder = db.drama_dir(d)
    _write(os.path.join(folder, "source.mp3"), DATA)
    _write(os.path.join(folder, "source.mp4"), DATA)
    _write(os.path.join(folder, "dub_track.wav"), DATA)
    os.makedirs(os.path.join(folder, "exports", "subtitle"), exist_ok=True)
    _write(os.path.join(folder, "exports", "subtitle", "d.srt"), DATA)
    db.update_drama(d, audio_filename="source.mp3", source_video_filename="source.mp4")
    return d


def _urls(d):
    return {"audio": f"/api/media/dramas/{d}/audio",
            "video": f"/api/media/dramas/{d}/video",
            "dub": f"/api/dub/dramas/{d}/track",
            "artifact": f"/api/artifacts/dramas/{d}/subtitle"}


ALL = ("audio", "video", "dub", "artifact")


def _no_leak(r, *needles):
    body = r.content
    headers = " ".join(f"{k}: {v}" for k, v in r.headers.items())
    for n in needles:
        n = str(n)
        assert n.encode() not in body, f"{n!r} leaked in body"
        assert n not in headers, f"{n!r} leaked in headers"


# --- single ranges on every streaming route --------------------------------

@pytest.mark.parametrize("route", ALL)
@pytest.mark.parametrize("header,start,end", [
    ("bytes=300-", 300, SIZE),            # open-ended
    ("bytes=-10", SIZE - 10, SIZE),       # suffix
    ("bytes=-5000", 0, SIZE),             # suffix longer than the file = whole file
    ("bytes=1000-99999", 1000, SIZE),     # last-pos past EOF is clamped
    ("bytes=0-0", 0, 1),                  # first byte
    (f"bytes={SIZE - 1}-{SIZE - 1}", SIZE - 1, SIZE),  # last byte
    ("bytes=0-", 0, SIZE),                # whole file as a range
])
def test_single_range_every_route(client, did, route, header, start, end):
    r = client.get(_urls(did)[route], headers={"Range": header})
    assert r.status_code == 206
    assert r.content == DATA[start:end]
    assert r.headers["content-range"] == f"bytes {start}-{end - 1}/{SIZE}"
    assert r.headers["content-length"] == str(end - start)
    assert r.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize("route", ALL)
def test_no_range_is_full_200_with_accept_ranges(client, did, route):
    r = client.get(_urls(did)[route])
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(SIZE)
    assert "content-range" not in r.headers


def test_range_206_keeps_media_type_and_validators(client, did):
    full = client.get(_urls(did)["audio"])
    part = client.get(_urls(did)["audio"], headers={"Range": "bytes=5-9"})
    assert part.headers["content-type"] == full.headers["content-type"] == "audio/mpeg"
    assert part.headers["etag"] == full.headers["etag"]
    assert part.headers["last-modified"] == full.headers["last-modified"]
    assert part.headers["x-content-type-options"] == "nosniff"


# --- unsatisfiable ---------------------------------------------------------

@pytest.mark.parametrize("route", ALL)
@pytest.mark.parametrize("header", [f"bytes={SIZE}-", f"bytes={SIZE + 50}-{SIZE + 60}", "bytes=-0"])
def test_past_eof_is_416_with_star_content_range(client, did, route, header):
    """RFC 9110 15.5.17: 416 carries Content-Range: bytes */<size>. A
    zero-length suffix (bytes=-0) is unsatisfiable per 14.1.1."""
    r = client.get(_urls(did)[route], headers={"Range": header})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{SIZE}"
    assert DATA[:16] not in r.content


def test_empty_file_any_range_is_416(client, isolated_db, did):
    import db
    _write(os.path.join(db.drama_dir(did), "empty.mp3"), b"")
    db.update_drama(did, audio_filename="empty.mp3")
    r = client.get(_urls(did)["audio"])
    assert r.status_code == 200 and r.content == b"" and r.headers["content-length"] == "0"
    r = client.get(_urls(did)["audio"], headers={"Range": "bytes=0-"})
    assert r.status_code == 416 and r.headers["content-range"] == "bytes */0"


# --- malformed -------------------------------------------------------------

@pytest.mark.parametrize("header", [
    "bytes=abc-def", "bytes=5-2", "bytes=", "bytes", "bytes=--5", "bytes=1-2-3",
    "garbage", "bytes=0-4;x", b"bytes=\xc3\xa9-1",
])
def test_malformed_range_never_500_never_leaks(client, isolated_db, did, header):
    """RFC 9110 14.2: a server MAY ignore or reject an invalid
    ranges-specifier, so 200 (full), 400 or 416 are all acceptable."""
    import db
    for route in ALL:
        r = client.get(_urls(did)[route], headers={"Range": header})
        assert r.status_code in (200, 206, 400, 416), (route, r.status_code)
        if r.status_code == 200:
            assert r.content == DATA
        if r.status_code == 206:
            assert len(r.content) <= SIZE
        _no_leak(r, db.LIBRARY_DIR, "source.mp3", "dub_track.wav", "d.srt")


@pytest.mark.xfail(strict=True, reason=(
    "RFC 9110 14.2: 'An origin server MUST ignore a Range header field that contains a "
    "range unit it does not understand.' Starlette's FileResponse._parse_range_header "
    "raises MalformedRangeHeader -> 400 for any unit other than bytes."))
@pytest.mark.parametrize("route", ALL)
def test_unknown_range_unit_is_ignored(client, did, route):
    r = client.get(_urls(did)[route], headers={"Range": "items=0-5"})
    assert r.status_code == 200 and r.content == DATA


# --- multi-range -----------------------------------------------------------

def _parts(r):
    ctype = r.headers["content-type"]
    m = re.match(r"multipart/byteranges; boundary=(\S+)$", ctype)
    assert m, ctype
    boundary = m.group(1).encode()
    body = r.content
    assert body.endswith(b"--" + boundary + b"--")
    out = []
    for chunk in body.split(b"--" + boundary)[1:-1]:
        head, _, payload = chunk.partition(b"\r\n\r\n")
        assert payload.endswith(b"\r\n")
        out.append((head.decode("latin-1"), payload[:-2]))
    return out


@pytest.mark.parametrize("route", ALL)
def test_multi_range_parts_are_exact(client, did, route):
    r = client.get(_urls(did)[route], headers={"Range": "bytes=0-4, 100-109, -3"})
    assert r.status_code == 206
    assert int(r.headers["content-length"]) == len(r.content)
    parts = _parts(r)
    assert [p for _, p in parts] == [DATA[0:5], DATA[100:110], DATA[-3:]]
    ranges = [re.search(r"Content-Range: (.+)", h).group(1).strip() for h, _ in parts]
    assert ranges == ["bytes 0-4/1024", "bytes 100-109/1024", f"bytes {SIZE - 3}-{SIZE - 1}/1024"]
    if route == "audio":
        assert all("Content-Type: audio/mpeg" in h for h, _ in parts)


def test_overlapping_multi_range_is_coalesced(client, did):
    """RFC 9110 14.1.1 lets a server coalesce overlapping ranges."""
    r = client.get(_urls(did)["audio"], headers={"Range": "bytes=0-9,5-19"})
    assert r.status_code == 206
    if r.headers["content-type"].startswith("multipart/"):
        got = b"".join(p for _, p in _parts(r))
        assert DATA[0:20] in got or got == DATA[0:10] + DATA[5:20]
    else:
        assert r.content == DATA[0:20] and r.headers["content-range"] == "bytes 0-19/1024"


def test_too_many_ranges_does_not_amplify(client, did):
    """A request with a huge number of ranges must not produce a response
    much larger than the file (RFC 9110 14.2 / 17.15 denial-of-service note)."""
    header = "bytes=" + ",".join(f"{i}-{i}" for i in range(0, 1000, 2)) + ",0-1023"
    r = client.get(_urls(did)["audio"], headers={"Range": header})
    assert r.status_code in (200, 206, 416)
    assert len(r.content) <= 4 * SIZE


@pytest.mark.xfail(strict=True, reason=(
    "RFC 9110 14.1.1/15.5.17: a ranges-specifier is satisfiable if ANY range-spec is; 416 "
    "only when none are. Starlette's FileResponse._parse_range_header raises "
    "RangeNotSatisfiable when any single range starts past EOF."))
def test_multi_range_with_one_unsatisfiable_part_still_serves_the_rest(client, did):
    r = client.get(_urls(did)["audio"], headers={"Range": f"bytes=0-4,{SIZE + 10}-{SIZE + 20}"})
    assert r.status_code in (200, 206)
    assert DATA[0:5] in r.content


# --- If-Range --------------------------------------------------------------

@pytest.mark.parametrize("route", ALL)
def test_if_range_etag_and_date(client, did, route):
    url = _urls(did)[route]
    full = client.get(url)
    etag, lm = full.headers["etag"], full.headers["last-modified"]
    for validator in (etag, lm):
        r = client.get(url, headers={"Range": "bytes=10-19", "If-Range": validator})
        assert r.status_code == 206 and r.content == DATA[10:20], validator
    # Weak tag never matches (strong comparison, 13.1.5); stale date neither.
    for validator in ("W/" + etag, "Mon, 01 Jan 2001 00:00:00 GMT"):
        r = client.get(url, headers={"Range": "bytes=10-19", "If-Range": validator})
        assert r.status_code == 200 and r.content == DATA, validator


def test_if_range_without_range_is_ignored(client, did):
    etag = client.get(_urls(did)["audio"]).headers["etag"]
    for v in (etag, '"stale"'):
        r = client.get(_urls(did)["audio"], headers={"If-Range": v})
        assert r.status_code == 200 and r.content == DATA


def test_if_range_after_file_replaced_returns_new_full_file(client, isolated_db, did):
    """A player resuming a seek with the old ETag after the file was replaced
    (re-upload) must get the whole new file, never a slice of it spliced onto
    its cached old bytes."""
    import db
    url = _urls(did)["audio"]
    old = client.get(url).headers["etag"]
    new = b"NEW" * 500
    path = os.path.join(db.drama_dir(did), "source.mp3")
    _write(path, new)
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    head = client.head(url)
    assert head.headers["etag"] != old
    r = client.get(url, headers={"Range": "bytes=0-9", "If-Range": old})
    assert r.status_code == 200 and r.content == new


# --- validators ------------------------------------------------------------

@pytest.mark.parametrize("route", ALL)
def test_etag_and_last_modified_present_and_well_formed(client, did, route):
    r = client.get(_urls(did)[route])
    assert re.fullmatch(r'"[^"]+"', r.headers["etag"]), r.headers["etag"]
    lm = email.utils.parsedate_to_datetime(r.headers["last-modified"])
    assert lm.tzinfo is not None


# --- HEAD ------------------------------------------------------------------

@pytest.mark.parametrize("route", ("audio", "video"))
def test_head_matches_get_headers_without_body(client, did, route):
    url = _urls(did)[route]
    g, h = client.get(url), client.head(url)
    assert h.status_code == 200 and h.content == b""
    for k in ("content-length", "accept-ranges", "content-type", "etag", "last-modified",
              "content-disposition", "x-content-type-options"):
        assert h.headers[k] == g.headers[k], k
    assert h.headers["content-length"] == str(SIZE)


@pytest.mark.parametrize("route", ("audio", "video"))
def test_head_missing_is_404_without_leak(client, isolated_db, did, route):
    import db
    os.remove(os.path.join(db.drama_dir(did), "source.mp3" if route == "audio" else "source.mp4"))
    r = client.head(_urls(did)[route])
    assert r.status_code == 404
    _no_leak(r, db.LIBRARY_DIR, "source.mp")


@pytest.mark.xfail(strict=True, reason=(
    "RFC 9110 14.2: 'A server MUST ignore a Range header field received with a request "
    "method that is unrecognized or for which range handling is not defined' -- GET is the "
    "only such method. Starlette's FileResponse.__call__ applies Range to HEAD too "
    "(206 / 416); tests/test_api_media_playback.py::test_head_with_range pins the 206."))
@pytest.mark.parametrize("header", ["bytes=10-19", f"bytes={SIZE + 5}-"])
def test_head_ignores_range(client, did, header):
    r = client.head(_urls(did)["audio"], headers={"Range": header})
    assert r.status_code == 200
    assert r.headers["content-length"] == str(SIZE)
    assert "content-range" not in r.headers


# --- a file larger than 2**32 bytes (sparse) -------------------------------

BIG = 2 ** 32 + 1000


@pytest.fixture
def big(isolated_db, did):
    import db
    path = os.path.join(db.drama_dir(did), "big.mp3")
    with open(path, "wb") as f:
        f.truncate(BIG)                       # sparse: no real disk blocks
        f.seek(2 ** 32)
        f.write(b"TAIL-MARKER")
    if os.stat(path).st_blocks * 512 > 64 * 1024 * 1024:
        os.remove(path)
        pytest.skip("filesystem does not support sparse files")
    db.update_drama(did, audio_filename="big.mp3")
    yield did
    os.remove(path)


def test_over_4gib_head_and_seek(client, big):
    url = _urls(big)["audio"]
    h = client.head(url)
    assert h.status_code == 200 and h.headers["content-length"] == str(BIG)
    r = client.get(url, headers={"Range": f"bytes={2 ** 32}-{2 ** 32 + 10}"})
    assert r.status_code == 206 and r.content == b"TAIL-MARKER"
    assert r.headers["content-range"] == f"bytes {2 ** 32}-{2 ** 32 + 10}/{BIG}"
    assert r.headers["content-length"] == "11"
    r = client.get(url, headers={"Range": "bytes=-4"})
    assert r.status_code == 206 and r.content == b"\0" * 4
    assert r.headers["content-range"] == f"bytes {BIG - 4}-{BIG - 1}/{BIG}"
    r = client.get(url, headers={"Range": f"bytes={BIG}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{BIG}"
    r = client.get(url, headers={"Range": f"bytes={BIG - 3}-"})
    assert r.status_code == 206 and r.content == b"\0" * 3
    r = client.get(url, headers={"Range": f"bytes={2 ** 32}-{2 ** 32 + 3},{2 ** 32 + 7}-{2 ** 32 + 10}"})
    assert r.status_code == 206
    assert [p for _, p in _parts(r)] == [b"TAIL", b"RKER"]
    assert int(r.headers["content-length"]) == len(r.content)


# --- missing media, missing drama, traversal -------------------------------

def test_drama_without_any_media_is_404_everywhere(client, isolated_db):
    import db
    d = db.create_drama(title_en="Empty")
    for route, url in _urls(d).items():
        for headers in ({}, {"Range": "bytes=0-"}):
            r = client.get(url, headers=headers)
            assert r.status_code == 404, (route, r.status_code)
            _no_leak(r, db.LIBRARY_DIR, db.drama_dir(d))


def test_unknown_drama_is_404_everywhere(client, isolated_db):
    import db
    for route, url in _urls(987654).items():
        r = client.get(url, headers={"Range": "bytes=0-"})
        assert r.status_code == 404, route
        _no_leak(r, db.LIBRARY_DIR)


@pytest.mark.parametrize("route,name", [("audio", "source.mp3"), ("video", "source.mp4"),
                                        ("dub", "dub_track.wav"),
                                        ("artifact", os.path.join("exports", "subtitle", "d.srt"))])
def test_file_deleted_after_db_row_is_404_without_path(client, isolated_db, did, route, name):
    import db
    os.remove(os.path.join(db.drama_dir(did), name))
    r = client.get(_urls(did)[route], headers={"Range": "bytes=0-9"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    _no_leak(r, db.LIBRARY_DIR, db.drama_dir(did), os.path.basename(name))


@pytest.mark.parametrize("path", [
    "/api/media/dramas/{d}/..%2F..%2Fsecret.mp3",
    "/api/media/dramas/{d}/%2e%2e",
    "/api/media/dramas/{d}/audio%2F..%2F..%2Fsecret.mp3",
    "/api/media/dramas/..%2F{d}/audio",
    "/api/media/dramas/0/audio",
    "/api/media/dramas/-1/audio",
    "/api/media/dramas/{d}/subtitle",
    "/api/artifacts/dramas/{d}/..",
    "/api/artifacts/dramas/{d}/..%2F..%2F..%2Fsecret.mp3",
    "/api/artifacts/dramas/{d}/%2e%2e%2f%2e%2e%2fsecret.mp3",
    "/api/artifacts/dramas/{d}/subtitle%2F..%2F..%2F..%2Fsecret.mp3",
    "/api/artifacts/dramas/{d}/exports",
    "/api/artifacts/dramas/{d}/" + "a" * 41,
    "/api/dub/dramas/{d}/track%2F..%2F..%2Fsecret.mp3",
    "/api/dub/dramas/{d}/..%2Ftrack",
])
def test_traversal_via_url_never_serves_outside_file(client, isolated_db, did, path):
    import db
    secret = os.path.realpath(os.path.join(db.LIBRARY_DIR, "secret.mp3"))
    _write(secret, b"TOP-SECRET-BYTES")
    r = client.get(path.format(d=did), headers={"Range": "bytes=0-"})
    assert r.status_code in (400, 404, 405, 422), r.status_code
    assert b"TOP-SECRET-BYTES" not in r.content
    _no_leak(r, db.LIBRARY_DIR, secret, db.drama_dir(did))


def test_traversal_via_stored_video_name(client, isolated_db, did, tmp_path):
    """Same containment for the video field (the slice's own test only
    exercises audio_filename)."""
    import db
    outside = os.path.realpath(os.path.join(db.drama_dir(did), "..", "outside.mp4"))
    _write(outside, b"OUTSIDE-VIDEO")
    for name in ("../outside.mp4", outside, "./../outside.mp4"):
        db.update_drama(did, source_video_filename=name)
        r = client.get(_urls(did)["video"], headers={"Range": "bytes=0-"})
        assert r.status_code == 404 and b"OUTSIDE-VIDEO" not in r.content, name
        _no_leak(r, outside, db.drama_dir(did))


def test_dub_track_symlink_escape_refused(client, isolated_db, did, tmp_path):
    import db
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"SECRET-WAV")
    track = os.path.join(db.drama_dir(did), "dub_track.wav")
    os.remove(track)
    try:
        os.symlink(secret, track)
    except OSError:
        pytest.skip("symlinks unavailable")
    r = client.get(_urls(did)["dub"], headers={"Range": "bytes=0-"})
    assert r.status_code == 404 and b"SECRET-WAV" not in r.content
    _no_leak(r, str(tmp_path), db.drama_dir(did))


def test_artifact_symlinked_kind_folder_refused(client, isolated_db, did, tmp_path):
    import db
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "leak.bin").write_bytes(b"SECRET-ARTIFACT")
    try:
        os.symlink(elsewhere, os.path.join(db.drama_dir(did), "exports", "epub"))
    except OSError:
        pytest.skip("symlinks unavailable")
    r = client.get(f"/api/artifacts/dramas/{did}/epub", headers={"Range": "bytes=0-"})
    assert r.status_code == 404 and b"SECRET-ARTIFACT" not in r.content
    _no_leak(r, str(tmp_path), db.drama_dir(did))


# --- permission (BAIHE_API_AUTH=on) ----------------------------------------

def test_media_stream_permission_on_dub_and_artifact_and_range(isolated_db, did):
    """The slice's own permission test covers audio/video only; this adds
    the dub track and artifact routes, and checks a refused Range request
    never leaks bytes or a Content-Range."""
    from api import auth as api_auth
    from services import auth_service
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)

    def hdrs(session):
        return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
                api_auth.CSRF_HEADER: session["csrf_token"], "Range": "bytes=0-9"}
    reader = auth_service.add_user("reader@example.com")
    auth_service.grant_permission(reader["id"], "library.read")  # not enough
    no = hdrs(auth_service.create_session(reader["id"]))
    viewer = auth_service.add_user("viewer@example.com")
    auth_service.grant_permission(viewer["id"], "media.stream")
    yes = hdrs(auth_service.create_session(viewer["id"]))
    for route, url in _urls(did).items():
        anon = c.get(url, headers={"Range": "bytes=0-9"})
        assert anon.status_code == 401, route
        denied = c.get(url, headers=no)
        assert denied.status_code == 403, route
        for r in (anon, denied):
            assert DATA[:10] not in r.content and "content-range" not in r.headers
        ok = c.get(url, headers=yes)
        assert ok.status_code == 206 and ok.content == DATA[:10], route
    for route in ("audio", "video"):
        assert c.head(_urls(did)[route], headers={k: v for k, v in no.items() if k != "Range"}
                      ).status_code == 403
