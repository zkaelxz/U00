"""
tests/test_subtitle_import_service.py -- importing a subtitle file into a
title (service and HTTP contract), the confirmations and history snapshot,
permissions and ownership, the LRC export route and the import-subtitle CLI.
"""


import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import db
import subtitle_parse
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service
from services import subtitle_import_service as svc
from lib.errors import InvalidInputError

SRT = ("1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n"
       "2\n00:00:03,000 --> 00:00:04,000\nsecond\n\n"
       "3\n00:00:05,000 --> 00:00:06,000\nthird\n").encode()
B = "/api/subtitle-import/dramas"


def _drama(lines=(), **kw):
    did = db.create_drama(title_en="D", source_language=kw.pop("source_language", "zh"), **kw)
    if lines:
        db.save_lines(did, list(lines))
    return did


def _three_lines():
    return [Line(idx=0, start=0.9, end=2.1, zh="一", en=""), Line(idx=1, start=2.9, end=4.2, zh="二", en="old"),
            Line(idx=2, start=4.9, end=6.1, zh="三", en="")]


def _upload(local_client, did, route, data=SRT, name="a.srt", **fields):
    return local_client.post(f"{B}/{did}/{route}", files={"file": (name, data)},
                       data={k: (str(v).lower() if isinstance(v, bool) else v) for k, v in fields.items()})


class TestSourceImport:
    def test_into_an_empty_title(self, local_client):
        did = _drama()
        r = _upload(local_client, did, "apply")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lines_written"] == 3 and body["undo"] is None and body["encoding"] == "utf-8"
        rows = db.load_lines(did)
        assert [(x["start"], x["end"], x["zh"]) for x in rows] == [(1.0, 2.0, "first"), (3.0, 4.0, "second"), (5.0, 6.0, "third")]
        assert body["line_ids"] == [x["id"] for x in rows] and all(body["line_ids"])
        assert db.get_drama(did)["status"] == "aligned"

    def test_replacing_lines_needs_confirmation_and_snapshots_first(self, local_client):
        did = _drama(_three_lines())
        before = [x["id"] for x in db.load_lines(did)]
        r = _upload(local_client, did, "apply")
        assert r.status_code == 422
        assert r.json()["error"]["details"]["reason"] == "confirm_replace_lines"
        assert [x["id"] for x in db.load_lines(did)] == before  # nothing written
        r = _upload(local_client, did, "apply", confirm_replace_lines=True)
        assert r.status_code == 200
        assert r.json()["undo"]["history_id"]
        assert [x["zh"] for x in db.load_lines(did)] == ["first", "second", "third"]
        assert db.list_line_history(did)  # the old lines are restorable

    def test_bilingual_split_fills_translation_too(self, local_client):
        data = b"1\n00:00:01,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd\nHello\n"
        did = _drama()
        r = _upload(local_client, did, "apply", data=data, split_bilingual=True)
        assert r.status_code == 200
        row = db.load_lines(did)[0]
        assert (row["zh"], row["en"]) == ("你好", "Hello")

    def test_blocking_file_writes_nothing(self, local_client):
        bad = b"1\n00:00:01,000 --> 00:00:02,000\nok\n\n2\n00:00:05,000 --> 00:00:04,000\nbackwards\n"
        did = _drama(_three_lines())
        r = _upload(local_client, did, "apply", data=bad, confirm_replace_lines=True)
        assert r.status_code == 422 and "stop the import" in r.json()["error"]["message"]
        assert [x["zh"] for x in db.load_lines(did)] == ["一", "二", "三"]

    def test_a_running_job_blocks_the_write(self, local_client, monkeypatch):
        did = _drama()
        monkeypatch.setattr("services.restructure_service._refuse_if_job_running",
                            lambda d: (_ for _ in ()).throw(svc.InvalidInputError("busy")))
        assert _upload(local_client, did, "apply").status_code == 422
        assert db.load_lines(did) == []


class TestTranslationImport:
    EN = ("1\n00:00:01,000 --> 00:00:02,000\nOne\n\n"
          "2\n00:00:03,000 --> 00:00:04,000\nTwo\n\n"
          "3\n00:00:04,500 --> 00:00:05,500\nThree a\n\n"
          "4\n00:00:05,500 --> 00:00:06,000\nthree b\n\n"
          "5\n00:00:30,000 --> 00:00:31,000\nstray\n").encode()

    def test_matches_by_time_overlap_and_keeps_ids_and_other_fields(self, local_client):
        did = _drama(_three_lines())
        before = db.load_lines(did)
        r = _upload(local_client, did, "apply", data=self.EN, mode="translation", confirm_overwrite=True)
        assert r.status_code == 200, r.text
        assert r.json()["matched_lines"] == 3 and r.json()["unmatched_cues"] == 1
        after = db.load_lines(did)
        assert [x["id"] for x in after] == [x["id"] for x in before]
        assert [x["en"] for x in after] == ["One", "Two", "Three a three b"]
        assert [(x["start"], x["end"], x["zh"]) for x in after] == [(x["start"], x["end"], x["zh"]) for x in before]

    def test_overwriting_an_existing_translation_needs_confirmation(self, local_client):
        did = _drama(_three_lines())
        r = _upload(local_client, did, "apply", data=self.EN, mode="translation")
        assert r.status_code == 422 and r.json()["error"]["details"] == {"reason": "confirm_overwrite", "overwrites": 1}
        assert [x["en"] for x in db.load_lines(did)] == ["", "old", ""]
        assert db.list_line_history(did) == []
        _upload(local_client, did, "apply", data=self.EN, mode="translation", confirm_overwrite=True)
        assert db.list_line_history(did)[0]["label"] == svc.SNAPSHOT_LABEL

    def test_filling_every_line_marks_the_title_translated(self, local_client):
        did = _drama(_three_lines(), status="aligned")
        r = _upload(local_client, did, "apply", data=self.EN, mode="translation", confirm_overwrite=True)
        assert r.status_code == 200, r.text
        assert db.get_drama(did)["status"] == "translated"

    def test_a_partial_fill_leaves_the_title_aligned(self, local_client):
        partial = self.EN.decode().split("\n\n")[0].encode()
        did = _drama(_three_lines(), status="aligned")
        assert _upload(local_client, did, "apply", data=partial, mode="translation").status_code == 200
        assert db.get_drama(did)["status"] == "aligned"

    def test_fills_empty_lines_without_confirmation(self, local_client):
        lines = _three_lines()
        lines[1].en = ""
        did = _drama(lines)
        assert _upload(local_client, did, "apply", data=self.EN, mode="translation").status_code == 200

    def test_needs_existing_lines(self, local_client):
        did = _drama()
        r = _upload(local_client, did, "apply", data=self.EN, mode="translation")
        assert r.status_code == 422 and "no lines yet" in r.json()["error"]["message"]
        assert _upload(local_client, did, "preview", data=self.EN, mode="translation").json()["blocked_reason"]

    def test_bilingual_file_uses_the_translation_half(self, local_client):
        data = b"1\n00:00:01,000 --> 00:00:02,000\n\xe4\xb8\x80\nOne\n"
        did = _drama(_three_lines())
        _upload(local_client, did, "apply", data=data, mode="translation", split_bilingual=True, confirm_overwrite=True)
        assert db.load_lines(did)[0]["en"] == "One"


class TestPreview:
    def test_reports_the_file_and_changes_nothing(self, local_client):
        did = _drama(_three_lines())
        r = _upload(local_client, did, "preview")
        assert r.status_code == 200
        body = r.json()
        assert (body["format"], body["cue_count"], body["encoding"], body["blocking"]) == ("srt", 3, "utf-8", False)
        assert body["replaces_lines"] == 3 and body["sample"][0]["text"] == "first"
        assert [x["zh"] for x in db.load_lines(did)] == ["一", "二", "三"]

    def test_problems_and_a_legacy_encoding_are_visible(self, local_client):
        data = ("1\n00:00:03,000 --> 00:00:04,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n世界\n").encode("gb18030")
        body = _upload(local_client, _drama(), "preview", data=data, name="x.srt").json()
        codes = {p["code"] for p in body["problems"]}
        assert {"out_of_order", "encoding_guess"} <= codes
        assert body["encoding"] == "gb18030" and body["encoding_guessed"] and body["detected_language"] == "zh"

    def test_unusable_file_is_a_plain_422(self, local_client):
        r = _upload(local_client, _drama(), "preview", data=b"hello", name="x.txt")
        assert r.status_code == 422 and "look like" in r.json()["error"]["message"]


class TestUploadBounds:
    def test_oversize_is_413_before_parsing(self, local_client):
        big = b"x" * (subtitle_parse.MAX_FILE_BYTES + 100_000)
        assert _upload(local_client, _drama(), "preview", data=big).status_code == 413

    def test_missing_file_part_is_422(self, local_client):
        r = local_client.post(f"{B}/{_drama()}/preview", data={"mode": "source"})
        assert r.status_code == 422

    def test_responses_carry_no_paths(self, local_client):
        text = _upload(local_client, _drama(), "preview", name="C:\\Users\\me\\secret\\a.srt").text
        assert "secret" not in text and "Users" not in text


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
        local_client = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        return local_client, db.create_drama(title_en="A", source_language="zh", owner_user_id=a_id, is_private=1), a, b

    def test_remote_callers_cannot_upload_even_the_owner(self, world):
        local_client, did, a, b = world
        for headers in (a, b):
            for route in ("preview", "apply"):
                r = local_client.post(f"{B}/{did}/{route}", files={"file": ("a.srt", SRT)}, headers=headers)
                assert r.status_code == 403, (route, r.status_code)
        assert db.load_lines(did) == []

    def test_signed_out_and_without_lines_edit_are_refused(self, world):
        local_client, did, a, _ = world
        assert local_client.post(f"{B}/{did}/preview", files={"file": ("a.srt", SRT)}).status_code in (401, 403)
        u = auth_service.add_user("viewer@example.com")
        s = auth_service.create_session(u["id"])
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}
        r = local_client.post(f"{B}/{did}/preview", files={"file": ("a.srt", SRT)}, headers=h)
        assert r.status_code in (403, 404)


class TestLrcExportRoute:
    def test_download_and_round_trip_through_import(self, local_client):
        did = _drama([Line(idx=0, start=1.0, end=2.0, zh="一", en="alpha"),
                      Line(idx=1, start=3.0, end=4.0, zh="二", en="beta")])
        r = local_client.get(f"/api/export/dramas/{did}/subtitle", params={"fmt": "lrc", "field": "en"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        disposition = r.headers["content-disposition"]
        assert 'subtitles (en).lrc"' in disposition and disposition.endswith(".lrc")
        assert r.text == "[00:01.00]alpha\n[00:02.00]\n[00:03.00]beta\n[00:04.00]\n"
        other = _drama()
        _upload(local_client, other, "apply", data=r.content, name="x.lrc")
        assert [(x["start"], x["end"], x["zh"]) for x in db.load_lines(other)] == [(1.0, 2.0, "alpha"), (3.0, 4.0, "beta")]

    def test_unknown_format_is_still_rejected(self, local_client):
        did = _drama()
        assert local_client.get(f"/api/export/dramas/{did}/subtitle", params={"fmt": "sbv"}).status_code == 422


def test_nul_in_an_encoding_name_is_a_clean_input_error(isolated_db):
    did = _drama()
    with pytest.raises(InvalidInputError):
        svc.preview_import(did, b"1\n00:00:01,000 --> 00:00:02,000\nx\n", "a.srt", encoding="utf\x008")


def test_translation_import_keeps_a_translation_edited_after_the_load(isolated_db, monkeypatch):
    did = _drama(_three_lines())
    real_snapshot = db.save_line_history_snapshot

    def edit_then_snapshot(*a, **k):
        # Another writer saves line 0's translation after the import loaded the lines.
        db.save_lines(did, [ln for ln in db.load_line_objects(did)[:1] if not setattr(ln, "en", "typed")],
                      fields=("en",))
        return real_snapshot(*a, **k)

    monkeypatch.setattr(db, "save_line_history_snapshot", edit_then_snapshot)
    data = b"1\n00:00:01,000 --> 00:00:02,000\nOne\n\n2\n00:00:03,000 --> 00:00:04,000\nTwo\n"
    out = svc.import_subtitle(did, data, "a.srt", mode="translation", confirm_overwrite=True)
    assert [r["en"] for r in db.load_lines(did)][:2] == ["typed", "Two"]
    assert out["lines_written"] == 1



# ---- imported ASS styles, positions and override tags --------------------

_STYLE_FORMAT = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                 "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                 "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
_STYLE_ROWS = [
    "Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,30,30,40,1",
    "Style: Sign,Noto Sans,48,&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,8,20,20,15,1"]
_SIGN_TEXT = r"{\an8}{\pos(10,20)}Hello {\i1}there{\i0}"
_KARAOKE_TEXT = r"{\k20}ka{\k30}ra"
ASS = ("[Script Info]\nTitle: T\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n"
       "YCbCr Matrix: TV.709\n\n[V4+ Styles]\n" + _STYLE_FORMAT + "\n" + "\n".join(_STYLE_ROWS) + "\n\n"
       "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       f"Dialogue: 2,0:00:01.00,0:00:02.00,Sign,Bob,5,6,7,Banner;1,{_SIGN_TEXT}\n"
       f"Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{_KARAOKE_TEXT}\n").encode()
_KNOWN_RECORDS = ("[", "Format:", "Style:", "Dialogue:", "Title:", "ScriptType:", "PlayResX:", "PlayResY:",
                  "WrapStyle:", "ScaledBorderAndShadow:", "YCbCr Matrix:")


def _ass_out(did, field="zh", **kw):
    from services import export_service
    return export_service.generate_ass_text(did, field=field, **kw)


def _dialogues(text):
    return [row for row in text.splitlines() if row.startswith("Dialogue:")]


def _events(text):
    """Each Dialogue as its 10 comma-split fields (Text keeps its commas)."""
    return [row[len("Dialogue: "):].split(",", 9) for row in _dialogues(text)]


def _assert_only_known_records(text, dialogue_count):
    for row in text.splitlines():
        assert not row.strip() or row.startswith(_KNOWN_RECORDS), repr(row)
    assert len(_dialogues(text)) == dialogue_count


def _import_ass(local_client, did=None, data=ASS, **fields):
    did = did or _drama()
    r = _upload(local_client, did, "apply", data=data, name="a.ass", **fields)
    assert r.status_code == 200, r.text
    return did, r.json()


def _set_en(did, texts):
    lines = db.load_line_objects(did)
    for ln, text in zip(lines, texts):
        ln.en = text
    db.save_lines(did, lines, fields=("en",))


class TestAssRoundTrip:
    def test_styles_canvas_and_event_fields_come_back(self, local_client):
        did, _ = _import_ass(local_client)
        out = _ass_out(did, "zh")
        out_lines = out.splitlines()
        for row in _STYLE_ROWS:
            assert row in out_lines
        for row in ("PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 2", "YCbCr Matrix: TV.709"):
            assert row in out_lines
        first, second = _events(out)
        assert first[0] == "2" and first[3:9] == ["Sign", "Bob", "5", "6", "7", "Banner;1"]
        assert second[0] == "0" and second[3] == "Default"

    def test_unedited_source_export_keeps_inline_and_karaoke_tags(self, local_client):
        did, _ = _import_ass(local_client)
        first, second = _events(_ass_out(did, "zh"))
        assert first[9] == _SIGN_TEXT and second[9] == _KARAOKE_TEXT

    def test_translation_export_keeps_only_leading_overrides(self, local_client):
        did, _ = _import_ass(local_client)
        _set_en(did, ["Hi, you", "Sung"])
        first, second = _events(_ass_out(did, "en"))
        assert first[9] == r"{\an8}{\pos(10,20)}Hi, you"
        assert second[9] == r"{\k20}Sung"
        assert first[3] == "Sign" and first[0] == "2"  # style and layer still imported

    def test_editing_the_source_text_drops_inline_tags_but_keeps_leading(self, local_client):
        did, _ = _import_ass(local_client)
        lines = db.load_line_objects(did)
        lines[0].zh = "Hello world"
        db.save_lines(did, lines, fields=("zh",))
        first, second = _events(_ass_out(did, "zh"))
        assert first[9] == r"{\an8}{\pos(10,20)}Hello world"
        assert second[9] == _KARAOKE_TEXT  # untouched line still verbatim

    def test_baihe_styles_colliding_with_imported_names_are_renamed(self, local_client):
        did, _ = _import_ass(local_client)
        out = _ass_out(did, "zh")
        names = [row[len("Style: "):].split(",")[0] for row in out.splitlines() if row.startswith("Style:")]
        assert names.count("Default") == 1 and "Baihe Default" in names

    def test_a_line_added_later_uses_baihe_style_scaled_to_the_canvas(self, local_client):
        did, _ = _import_ass(local_client)
        lines = db.load_line_objects(did)
        lines.append(Line(idx=2, start=5.0, end=6.0, zh="新", en="new"))
        db.save_lines(did, lines, fields=None)
        out = _ass_out(did, "zh", preset="Clean")
        added = _events(out)[-1]
        assert added[3] == "Baihe Default" and added[9] == "新"
        baihe = next(r for r in out.splitlines() if r.startswith("Style: Baihe Default,")).split(",")
        assert baihe[2] == "90"  # Clean's 24 on a 288-high canvas, scaled to 1080

    def test_srt_and_vtt_export_carry_no_tags(self, local_client):
        from services import export_service
        did, _ = _import_ass(local_client)
        for fmt in ("srt", "vtt"):
            text = export_service.generate_subtitle_text(did, fmt, "zh")
            assert "Hello there" in text and "kara" in text
            assert "\\" not in text and "{" not in text

    def test_importing_an_srt_afterwards_drops_the_styles_from_the_export(self, local_client):
        did, _ = _import_ass(local_client)
        assert db.get_drama(did)["ass_styles"]
        r = _upload(local_client, did, "apply", confirm_replace_lines=True)
        assert r.status_code == 200
        assert all(x["ass_event"] is None for x in db.load_lines(did))
        out = _ass_out(did, "zh")
        assert "PlayResX: 384" in out and "PlayResY: 288" in out
        assert "Baihe Default" not in out and "Sign" not in out and "YCbCr" not in out

    def test_undoing_the_import_brings_back_lines_without_events(self, local_client):
        from services import restructure_service
        did = _drama(_three_lines())
        _, body = _import_ass(local_client, did, confirm_replace_lines=True)
        assert all(x["ass_event"] for x in db.load_lines(did))
        undo = body["undo"]
        restructure_service.restore_version(did, undo["history_id"], body["line_ids"],
                                            undo["lines_fingerprint"])
        rows = db.load_lines(did)
        assert [x["zh"] for x in rows] == ["一", "二", "三"]
        assert all(x["ass_event"] is None for x in rows)
        out = _ass_out(did, "zh")  # the stored styles no longer match any line
        assert "PlayResX: 384" in out and "Baihe Default" not in out

    def test_undoing_a_second_import_brings_back_the_first_files_styles(self, local_client):
        from services import restructure_service
        did, _ = _import_ass(local_client)
        before = _ass_out(did, "zh")
        other = ASS.replace(b"PlayResX: 1920", b"PlayResX: 640").replace(b"PlayResY: 1080", b"PlayResY: 360")
        other = other.replace(b"Style: Sign,", b"Style: Other,").replace(b",Sign,", b",Other,")
        _, body = _import_ass(local_client, did, data=other, confirm_replace_lines=True)
        assert "PlayResY: 360" in _ass_out(did, "zh")
        undo = body["undo"]
        restructure_service.restore_version(did, undo["history_id"], body["line_ids"],
                                            undo["lines_fingerprint"])
        assert _ass_out(did, "zh") == before

    def test_bilingual_split_keeps_the_event_on_the_lines(self, local_client):
        data = ASS.replace(r"Hello {\i1}there{\i0}".encode(), r"你好\NHello".encode())
        did, _ = _import_ass(local_client, data=data, split_bilingual=True)
        row = db.load_lines(did)[0]
        assert (row["zh"], row["en"]) == ("你好", "Hello") and row["ass_event"]
        first = _events(_ass_out(did, "en"))[0]
        assert first[3] == "Sign" and first[9] == r"{\an8}{\pos(10,20)}Hello"


class TestHostileStoredAssData:
    BAD = "a b\r\nDialogue: 0,0:00:00.00,0:00:09.00,X,,0,0,0,,INJECTED,[Events]"

    def _title(self, event, styles=None):
        did = _drama()
        db.save_lines(did, [Line(idx=0, start=1.0, end=2.0, zh="一", en="one", ass_event=event),
                            Line(idx=1, start=3.0, end=4.0, zh="二", en="two", ass_event=event)])
        db.update_drama(did, ass_styles=styles if styles is not None else
                        '{"styles": [], "script_info": {"playresx": "1920", "playresy": "1080"}}')
        return did

    @pytest.mark.parametrize("field", ["zh", "en"])
    def test_whitespace_and_commas_in_event_fields_cannot_add_records(self, isolated_db, field):
        import json
        bad = self.BAD
        did = self._title(json.dumps({k: bad for k in ("layer", "style", "name", "marginl", "marginr",
                                                       "marginv", "effect", "lead", "raw", "plain")}))
        _assert_only_known_records(_ass_out(did, field), 2)

    @pytest.mark.parametrize("event", ["{not json", "[1, 2]", '"text"', "123", "null"])
    def test_malformed_event_json_is_treated_as_no_event(self, isolated_db, event):
        did = self._title(event)
        out = _ass_out(did, "zh")
        _assert_only_known_records(out, 2)
        assert [e[9] for e in _events(out)] == ["一", "二"]

    @pytest.mark.parametrize("margin", ["1e999", "abc", "-1e999", "nan", ""])
    def test_non_numeric_margins_become_ints(self, isolated_db, margin):
        import json
        did = self._title(json.dumps({"marginl": margin, "marginr": margin, "marginv": margin, "layer": margin}))
        out = _ass_out(did, "zh")
        _assert_only_known_records(out, 2)
        for e in _events(out):
            assert all(x.lstrip("-").isdigit() for x in [e[0], *e[5:8]])

    def test_hostile_stored_styles_cannot_add_records(self, isolated_db):
        import json
        evil = "x\nDialogue: 0,0:00:00.00,0:00:09.00,Default,,0,0,0,,INJECTED\r[Events]"
        styles = json.dumps({"styles": [{"name": evil, "fontname": evil, "fontsize": evil, "bold": evil},
                                        {"name": "Sign", "alignment": "8\nStyle: Evil,Arial"}],
                             "script_info": {"playresx": evil, "playresy": evil, "wrapstyle": evil,
                                             "ycbcr matrix": evil, "scaledborderandshadow": evil}})
        did = self._title('{"style": "Sign"}', styles)
        _assert_only_known_records(_ass_out(did, "zh"), 2)

    @pytest.mark.parametrize("styles", ["{bad", "[1]", "5", '{"styles": "x", "script_info": 3}',
                                        '{"styles": [1, null, {"name": 5}], "script_info": []}'])
    def test_malformed_stored_styles_export_baihe_output(self, isolated_db, styles):
        did = self._title('{"style": "Sign"}', styles)
        out = _ass_out(did, "zh")
        _assert_only_known_records(out, 2)
