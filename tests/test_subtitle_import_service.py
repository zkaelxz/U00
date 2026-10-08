"""
tests/test_subtitle_import_service.py -- importing a subtitle file into a
title (service and HTTP contract), the confirmations and history snapshot,
permissions and ownership, the LRC export route and the import-subtitle CLI.
"""

import argparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import cli
import cli_subtitle
import db
import subtitle_parse
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service
from services import subtitle_import_service as svc
from services.service_errors import InvalidInputError

SRT = ("1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nsecond\n\n"
       "3\n00:00:05,000 --> 00:00:06,000\nthird\n").encode()
B = "/api/subtitle-import/dramas"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _drama(lines=(), **kw):
    did = db.create_drama(title_en="D", source_language=kw.pop("source_language", "zh"), **kw)
    if lines:
        db.save_lines(did, list(lines))
    return did


def _three_lines():
    return [Line(idx=0, start=0.9, end=2.1, zh="一", en=""), Line(idx=1, start=2.9, end=4.2, zh="二", en="old"),
            Line(idx=2, start=4.9, end=6.1, zh="三", en="")]


def _upload(client, did, route, data=SRT, name="a.srt", **fields):
    return client.post(f"{B}/{did}/{route}", files={"file": (name, data)},
                       data={k: (str(v).lower() if isinstance(v, bool) else v) for k, v in fields.items()})


class TestSourceImport:
    def test_into_an_empty_title(self, client):
        did = _drama()
        r = _upload(client, did, "apply")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lines_written"] == 3 and body["undo"] is None and body["encoding"] == "utf-8"
        rows = db.load_lines(did)
        assert [(x["start"], x["end"], x["zh"]) for x in rows] == [(1.0, 2.0, "first"), (3.0, 4.0, "second"), (5.0, 6.0, "third")]
        assert body["line_ids"] == [x["id"] for x in rows] and all(body["line_ids"])
        assert db.get_drama(did)["status"] == "aligned"

    def test_replacing_lines_needs_confirmation_and_snapshots_first(self, client):
        did = _drama(_three_lines())
        before = [x["id"] for x in db.load_lines(did)]
        r = _upload(client, did, "apply")
        assert r.status_code == 422
        assert r.json()["error"]["details"]["reason"] == "confirm_replace_lines"
        assert [x["id"] for x in db.load_lines(did)] == before  # nothing written
        r = _upload(client, did, "apply", confirm_replace_lines=True)
        assert r.status_code == 200
        assert r.json()["undo"]["history_id"]
        assert [x["zh"] for x in db.load_lines(did)] == ["first", "second", "third"]
        assert db.list_line_history(did)  # the old lines are restorable

    def test_bilingual_split_fills_translation_too(self, client):
        data = b"1\n00:00:01,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd\nHello\n"
        did = _drama()
        r = _upload(client, did, "apply", data=data, split_bilingual=True)
        assert r.status_code == 200
        row = db.load_lines(did)[0]
        assert (row["zh"], row["en"]) == ("你好", "Hello")

    def test_blocking_file_writes_nothing(self, client):
        bad = b"1\n00:00:01,000 --> 00:00:02,000\nok\n\n2\n00:00:05,000 --> 00:00:04,000\nbackwards\n"
        did = _drama(_three_lines())
        r = _upload(client, did, "apply", data=bad, confirm_replace_lines=True)
        assert r.status_code == 422 and "stop the import" in r.json()["error"]["message"]
        assert [x["zh"] for x in db.load_lines(did)] == ["一", "二", "三"]

    def test_a_running_job_blocks_the_write(self, client, monkeypatch):
        did = _drama()
        monkeypatch.setattr("services.restructure_service._refuse_if_job_running",
                            lambda d: (_ for _ in ()).throw(svc.InvalidInputError("busy")))
        assert _upload(client, did, "apply").status_code == 422
        assert db.load_lines(did) == []


class TestTranslationImport:
    EN = ("1\n00:00:01,000 --> 00:00:02,000\nOne\n\n"
          "2\n00:00:03,000 --> 00:00:04,000\nTwo\n\n"
          "3\n00:00:04,500 --> 00:00:05,500\nThree a\n\n"
          "4\n00:00:05,500 --> 00:00:06,000\nthree b\n\n"
          "5\n00:00:30,000 --> 00:00:31,000\nstray\n").encode()

    def test_matches_by_time_overlap_and_keeps_ids_and_other_fields(self, client):
        did = _drama(_three_lines())
        before = db.load_lines(did)
        r = _upload(client, did, "apply", data=self.EN, mode="translation", confirm_overwrite=True)
        assert r.status_code == 200, r.text
        assert r.json()["matched_lines"] == 3 and r.json()["unmatched_cues"] == 1
        after = db.load_lines(did)
        assert [x["id"] for x in after] == [x["id"] for x in before]
        assert [x["en"] for x in after] == ["One", "Two", "Three a three b"]
        assert [(x["start"], x["end"], x["zh"]) for x in after] == [(x["start"], x["end"], x["zh"]) for x in before]

    def test_overwriting_an_existing_translation_needs_confirmation(self, client):
        did = _drama(_three_lines())
        r = _upload(client, did, "apply", data=self.EN, mode="translation")
        assert r.status_code == 422 and r.json()["error"]["details"] == {"reason": "confirm_overwrite", "overwrites": 1}
        assert [x["en"] for x in db.load_lines(did)] == ["", "old", ""]
        assert db.list_line_history(did) == []
        _upload(client, did, "apply", data=self.EN, mode="translation", confirm_overwrite=True)
        assert db.list_line_history(did)[0]["label"] == svc.SNAPSHOT_LABEL

    def test_fills_empty_lines_without_confirmation(self, client):
        lines = _three_lines()
        lines[1].en = ""
        did = _drama(lines)
        assert _upload(client, did, "apply", data=self.EN, mode="translation").status_code == 200

    def test_needs_existing_lines(self, client):
        did = _drama()
        r = _upload(client, did, "apply", data=self.EN, mode="translation")
        assert r.status_code == 422 and "no lines yet" in r.json()["error"]["message"]
        assert _upload(client, did, "preview", data=self.EN, mode="translation").json()["blocked_reason"]

    def test_bilingual_file_uses_the_translation_half(self, client):
        data = b"1\n00:00:01,000 --> 00:00:02,000\n\xe4\xb8\x80\nOne\n"
        did = _drama(_three_lines())
        _upload(client, did, "apply", data=data, mode="translation", split_bilingual=True, confirm_overwrite=True)
        assert db.load_lines(did)[0]["en"] == "One"


class TestPreview:
    def test_reports_the_file_and_changes_nothing(self, client):
        did = _drama(_three_lines())
        r = _upload(client, did, "preview")
        assert r.status_code == 200
        body = r.json()
        assert (body["format"], body["cue_count"], body["encoding"], body["blocking"]) == ("srt", 3, "utf-8", False)
        assert body["replaces_lines"] == 3 and body["sample"][0]["text"] == "first"
        assert [x["zh"] for x in db.load_lines(did)] == ["一", "二", "三"]

    def test_problems_and_a_legacy_encoding_are_visible(self, client):
        data = ("1\n00:00:03,000 --> 00:00:04,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n世界\n").encode("gb18030")
        body = _upload(client, _drama(), "preview", data=data, name="x.srt").json()
        codes = {p["code"] for p in body["problems"]}
        assert {"out_of_order", "encoding_guess"} <= codes
        assert body["encoding"] == "gb18030" and body["encoding_guessed"] and body["detected_language"] == "zh"

    def test_unusable_file_is_a_plain_422(self, client):
        r = _upload(client, _drama(), "preview", data=b"hello", name="x.txt")
        assert r.status_code == 422 and "look like" in r.json()["error"]["message"]


class TestUploadBounds:
    def test_oversize_is_413_before_parsing(self, client):
        big = b"x" * (subtitle_parse.MAX_FILE_BYTES + 100_000)
        assert _upload(client, _drama(), "preview", data=big).status_code == 413

    def test_missing_file_part_is_422(self, client):
        r = client.post(f"{B}/{_drama()}/preview", data={"mode": "source"})
        assert r.status_code == 422

    def test_responses_carry_no_paths(self, client):
        text = _upload(client, _drama(), "preview", name="C:\\Users\\me\\secret\\a.srt").text
        assert "secret" not in text and "Users" not in text


class TestSidecarsRoute:
    def test_ranks_names_and_never_binds(self, client):
        did = _drama(source_language="ja")
        r = client.post(f"{B}/{did}/sidecars", json={
            "media_name": "track.mp3", "names": ["track.en.srt", "track.ja.vtt", "track.mp3.vtt", "x.srt"]})
        assert r.status_code == 200
        body = r.json()
        assert body["ambiguous"] is True
        assert [c["name"] for c in body["candidates"]] == ["track.ja.vtt", "track.mp3.vtt", "track.en.srt"]

    def test_rejects_paths_in_names(self, client):
        r = client.post(f"{B}/{_drama()}/sidecars", json={"media_name": "t.mp3", "names": ["../t.srt"]})
        assert r.status_code == 422


class TestAccess:
    @pytest.fixture
    def world(self, isolated_db):
        def login(email, perms=True):
            u = auth_service.add_user(email)
            if perms:
                for p in auth_service.OPT_IN_PERMISSIONS:
                    auth_service.grant_permission(u["id"], p)
            s = auth_service.create_session(u["id"])
            return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                             api_auth.CSRF_HEADER: s["csrf_token"]}
        a_id, a = login("a@example.com")
        _, b = login("b@example.com")
        app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
        client = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        return client, db.create_drama(title_en="A", source_language="zh", owner_user_id=a_id, is_private=1), a, b

    def test_remote_callers_cannot_upload_even_the_owner(self, world):
        client, did, a, b = world
        for headers in (a, b):
            for route in ("preview", "apply"):
                r = client.post(f"{B}/{did}/{route}", files={"file": ("a.srt", SRT)}, headers=headers)
                assert r.status_code == 403, (route, r.status_code)
        assert db.load_lines(did) == []

    def test_sidecar_ranking_still_follows_ownership(self, world):
        client, did, a, b = world
        body = {"media_name": "ep1.mkv", "names": ["ep1.srt"]}
        assert client.post(f"{B}/{did}/sidecars", json=body, headers=b).status_code == 404
        assert client.post(f"{B}/{did}/sidecars", json=body, headers=a).status_code == 200

    def test_signed_out_and_without_lines_edit_are_refused(self, world):
        client, did, a, _ = world
        assert client.post(f"{B}/{did}/preview", files={"file": ("a.srt", SRT)}).status_code in (401, 403)
        u = auth_service.add_user("viewer@example.com")
        s = auth_service.create_session(u["id"])
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}
        r = client.post(f"{B}/{did}/preview", files={"file": ("a.srt", SRT)}, headers=h)
        assert r.status_code in (403, 404)


class TestLrcExportRoute:
    def test_download_and_round_trip_through_import(self, client):
        did = _drama([Line(idx=0, start=1.0, end=2.0, zh="一", en="alpha"),
                      Line(idx=1, start=3.0, end=4.0, zh="二", en="beta")])
        r = client.get(f"/api/export/dramas/{did}/subtitle", params={"fmt": "lrc", "field": "en"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        assert "drama_" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.lrc"')
        assert r.text == "[00:01.00]alpha\n[00:02.00]\n[00:03.00]beta\n[00:04.00]\n"
        other = _drama()
        _upload(client, other, "apply", data=r.content, name="x.lrc")
        assert [(x["start"], x["end"], x["zh"]) for x in db.load_lines(other)] == [(1.0, 2.0, "alpha"), (3.0, 4.0, "beta")]

    def test_unknown_format_is_still_rejected(self, client):
        did = _drama()
        assert client.get(f"/api/export/dramas/{did}/subtitle", params={"fmt": "sbv"}).status_code == 422


class TestCli:
    def _args(self, path, **kw):
        base = dict(id=None, file=str(path), mode="source", encoding=None, split_bilingual=False,
                    translation_first=False, yes=False, find=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_registered_on_the_real_parser(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["cli.py", "import-subtitle", "--help"])
        with pytest.raises(SystemExit):
            cli.main()
        assert "--split-bilingual" in capsys.readouterr().out

    def test_import_matches_the_service(self, isolated_db, tmp_path, capsys):
        f = tmp_path / "a.srt"
        f.write_bytes(SRT)
        did = _drama()
        cli_subtitle.cmd_import_subtitle(self._args(f, id=did))
        assert [x["zh"] for x in db.load_lines(did)] == ["first", "second", "third"]
        assert "Wrote 3 line(s) as source" in capsys.readouterr().out

    def test_replacing_needs_yes(self, isolated_db, tmp_path):
        f = tmp_path / "a.srt"
        f.write_bytes(SRT)
        did = _drama(_three_lines())
        with pytest.raises(SystemExit, match="--yes"):
            cli_subtitle.cmd_import_subtitle(self._args(f, id=did))
        assert [x["zh"] for x in db.load_lines(did)] == ["一", "二", "三"]
        cli_subtitle.cmd_import_subtitle(self._args(f, id=did, yes=True))
        assert [x["zh"] for x in db.load_lines(did)] == ["first", "second", "third"]

    def test_bad_file_changes_nothing(self, isolated_db, tmp_path):
        f = tmp_path / "a.srt"
        f.write_bytes(b"1\n00:00:05,000 --> 00:00:04,000\nx\n")
        did = _drama(_three_lines())
        with pytest.raises(SystemExit, match="stop the import|nothing was imported"):
            cli_subtitle.cmd_import_subtitle(self._args(f, id=did, yes=True))
        assert len(db.load_lines(did)) == 3

    def test_find_lists_candidates_without_importing(self, capsys):
        cli_subtitle.cmd_import_subtitle(self._args("", find=["track.mp3", "track.ja.vtt", "track.en.srt", "z.srt"]))
        out = capsys.readouterr().out
        assert "track.ja.vtt" in out and "2 candidates" in out and "z.srt" not in out
