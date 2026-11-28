"""
The extension's Show Baihe subs: the English lines of the caller's title made
from the open video page, for the page's own player (/subs on the loopback
bridge and on the device-token routes).
"""

import json

import pytest

import db
from core import Line
from services import extension_subs_service as subs
from services import export_service, media_upload_service, page_server

VIDEO_A = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
VIDEO_B = "https://www.youtube.com/watch?v=BBBBBBBBBBB"


def _title(link, owner=None, private=False, whole=True, lines=None, name="Sample title"):
    did = db.create_drama(title_zh=name, title_en=name, media_type="video",
                          owner_user_id=owner, is_private=int(private))
    from services import drama_service
    drama_service.set_source_url_once(did, link)
    if whole:
        ddir = db.drama_dir(did)
        open(f"{ddir}/video.mp4", "wb").close()
        media_upload_service._record_origin(ddir, link, ["video.mp4"])
    db.save_lines(did, lines if lines is not None else [
        Line(idx=0, start=1.0, end=2.5, zh="你好", en="Hello", speaker="SPEAKER_00"),
        Line(idx=1, start=3.0, end=4.0, zh="门", en="door slams", sfx=True),
        Line(idx=2, start=5.0, end=6.0, zh="还没翻", en=""),
    ])
    return did


def _ask(url, principal=subs._BRIDGE_PRINCIPAL):
    return subs.subs_for_page({"page_url": url}, principal=principal)


class TestMatching:
    def test_the_whole_download_of_this_page_is_found(self, isolated_db):
        _title(VIDEO_A)
        out = _ask(VIDEO_A + "&t=42s")
        assert out["found"] is True
        assert out["title"] == "Sample title"
        assert out["cues"] == [
            {"start": 1.0, "end": 2.5, "en": "Hello", "speaker": ""},
            {"start": 3.0, "end": 4.0, "en": "[door slams]", "speaker": ""},
        ]
        assert out["cut_off"] is False

    def test_two_youtube_videos_are_two_pages(self, isolated_db):
        _title(VIDEO_A, name="First")
        _title(VIDEO_B, name="Second")
        assert _ask(VIDEO_B)["title"] == "Second"
        assert _ask("https://www.youtube.com/watch?v=CCCCCCCCCCC") == {"found": False}

    def test_a_part_or_an_upload_is_not_a_match(self, isolated_db):
        # A part download and an upload leave no record of a whole download from the link.
        _title(VIDEO_A, whole=False)
        assert _ask(VIDEO_A) == {"found": False}

    def test_a_whole_title_is_found_past_an_older_part_of_the_same_page(self, isolated_db):
        _title(VIDEO_A, whole=False, name="Part")
        _title(VIDEO_A, name="Whole")
        assert _ask(VIDEO_A)["title"] == "Whole"

    def test_a_bare_site_page_names_no_title(self, isolated_db):
        _title(VIDEO_A)
        assert _ask("https://www.youtube.com/") == {"found": False}

    def test_a_title_with_no_english_yet_is_found_with_no_lines(self, isolated_db):
        _title(VIDEO_A, lines=[Line(idx=0, start=0, end=1, zh="你好", en="")])
        out = _ask(VIDEO_A)
        assert out["found"] is True and out["cues"] == []

    def test_the_oldest_title_with_english_wins_over_an_older_untranslated_one(self, isolated_db):
        _title(VIDEO_A, name="Untranslated", lines=[Line(idx=0, start=0, end=1, zh="你好", en="")])
        _title(VIDEO_A, name="Translated")
        _title(VIDEO_A, name="Also translated")
        assert _ask(VIDEO_A)["title"] == "Translated"

    @pytest.mark.parametrize("saved, page", [
        ("https://youtu.be/AAAAAAAAAAA?si=share", VIDEO_A),
        ("https://m.youtube.com/watch?v=AAAAAAAAAAA", VIDEO_A),
        ("https://youtube.com/watch?v=AAAAAAAAAAA", VIDEO_A),
        ("https://www.youtube.com/shorts/AAAAAAAAAAA", VIDEO_A),
        ("https://www.youtube.com/live/AAAAAAAAAAA?feature=share", VIDEO_A),
        (VIDEO_A, "https://youtu.be/AAAAAAAAAAA"),
        ("https://m.bilibili.com/video/BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD/"),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?p=1", "https://www.bilibili.com/video/BV1xx411c7mD"),
        ("https://www.bilibili.com/video/BV1xx411c7mD?p=2&spm=x", "https://m.bilibili.com/video/BV1xx411c7mD/?p=2"),
    ])
    def test_every_address_of_one_video_finds_its_title(self, isolated_db, saved, page):
        _title(saved)
        assert _ask(page)["found"] is True

    @pytest.mark.parametrize("saved, page", [
        ("https://youtu.be/AAAAAAAAAAA", VIDEO_B),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "https://www.bilibili.com/video/BV1xx411c7mD?p=2"),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "https://www.bilibili.com/video/BV1yy411c7mD"),
    ])
    def test_another_video_or_part_is_not_a_match(self, isolated_db, saved, page):
        _title(saved)
        assert _ask(page) == {"found": False}

    def test_the_duplicate_check_still_tells_the_addresses_apart(self):
        from services import drama_service
        assert drama_service._link_identity("https://youtu.be/AAAAAAAAAAA") != drama_service._link_identity(VIDEO_A)

    def test_speaker_names_are_the_ones_exports_use(self, isolated_db, monkeypatch):
        did = _title(VIDEO_A)
        monkeypatch.setattr(export_service, "_speaker_display_names",
                            lambda drama_id: {"SPEAKER_00": "Alex Example"} if drama_id == did else {})
        assert _ask(VIDEO_A)["cues"][0]["speaker"] == "Alex Example"

    def test_cues_are_capped(self, isolated_db, monkeypatch):
        monkeypatch.setattr(subs, "MAX_CUES", 1)
        _title(VIDEO_A)
        out = _ask(VIDEO_A)
        assert len(out["cues"]) == 1 and out["cut_off"] is True


class TestPrivacy:
    def test_nothing_about_the_link_or_files_goes_back(self, isolated_db):
        _title(VIDEO_A)
        text = json.dumps(_ask(VIDEO_A))
        assert "youtube" not in text and "AAAAAAAAAAA" not in text
        assert "video.mp4" not in text and "drama_id" not in text

    def test_another_members_private_title_is_not_found(self, isolated_db):
        from services import auth_service
        a = auth_service.add_user("a@example.com")
        b = auth_service.add_user("b@example.com")
        _title(VIDEO_A, owner=b["id"], private=True)
        member = {"user_id": a["id"], "is_admin": False}
        assert _ask(VIDEO_A, principal=member) == {"found": False}
        assert _ask(VIDEO_A, principal={"user_id": b["id"], "is_admin": False})["found"] is True


class TestBadInput:
    @pytest.mark.parametrize("payload", [{}, {"page_url": ""}, {"page_url": 5},
                                         {"page_url": "https://a.example/" + "x" * 3000}])
    def test_a_missing_or_bad_link_is_a_400(self, isolated_db, payload):
        with pytest.raises(page_server.EndpointError) as e:
            subs.subs_for_page(payload)
        assert e.value.status == 400


class TestLoopbackBridge:
    def test_the_pcs_own_extension_gets_the_lines(self, isolated_db):
        from tests.test_page_server import _post
        _title(VIDEO_A)
        r = _post(page_server.load_or_create_token(), {"page_url": VIDEO_A}, path="/subs")
        assert r.status == 200 and r.payload["found"] is True

    def test_without_the_token_it_is_refused(self, isolated_db):
        from tests.test_page_server import _post
        _title(VIDEO_A)
        assert _post(None, {"page_url": VIDEO_A}, path="/subs").status == 401


class TestDeviceTokenRoute:
    def test_a_member_gets_only_their_own_title(self, isolated_db):
        from tests.test_extension_bridge_routes import BASE, _auth, _client, _member
        a, token = _member("a@example.com")
        b, _ = _member("b@example.com")
        _title(VIDEO_A, owner=b["id"], private=True, name="Theirs")
        _title(VIDEO_B, owner=a["id"], private=True, name="Mine")
        client = _client()
        r = client.post(f"{BASE}/subs", headers=_auth(token), json={"page_url": VIDEO_A})
        assert r.status_code == 200 and r.json() == {"found": False}
        r = client.post(f"{BASE}/subs", headers=_auth(token), json={"page_url": VIDEO_B})
        assert r.status_code == 200 and r.json()["title"] == "Mine"
        assert r.headers["cache-control"] == "no-store"

    def test_without_lines_read_it_is_refused(self, isolated_db):
        from services import auth_service
        from tests.test_extension_bridge_routes import BASE, _auth, _client, _member
        a, token = _member("a@example.com")
        _title(VIDEO_A, owner=a["id"], private=True)
        auth_service.revoke_permission(a["id"], "lines.read")
        r = _client().post(f"{BASE}/subs", headers=_auth(token), json={"page_url": VIDEO_A})
        assert r.status_code == 403
