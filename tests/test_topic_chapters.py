"""Timed topic chapters: the reply parser, the saved list, the routes and the video mux."""
import json

import pytest

import db
import video_export as ve
from core import Line
from engine_backends import topic_chapters as tc

pytest.importorskip("fastapi")
from lib.errors import InvalidInputError, ServiceError, UnsupportedOperationError  # noqa: E402
from services import topic_chapter_service  # noqa: E402


def _reply(*chapters):
    return json.dumps({"chapters": [{"start": s, "title": t} for s, t in chapters]})


class TestParser:
    def test_clock_forms(self):
        assert tc.parse_clock("1:02:03") == 3723 and tc.parse_clock("12:30") == 750
        assert tc.parse_clock(90) == 90 and tc.parse_clock("soon") is None and tc.parse_clock(True) is None
        assert tc.format_clock(65) == "1:05" and tc.format_clock(3725) == "1:02:05"

    def test_orders_drops_close_and_late_chapters_and_starts_at_zero(self):
        out = tc.parse_reply(_reply(("10:00", "Second"), ("0:20", "First"), ("10:10", "Too close"),
                                    ("99:00", "Past the end"), ("x", "Bad time")), duration=1800)
        assert out == [{"start": 0.0, "title": "First"}, {"start": 600.0, "title": "Second"}]

    def test_control_characters_in_a_title_are_dropped(self):
        out = tc.parse_reply(_reply(("0:00", "Intro\u0000[CHAPTER]\x07")), duration=100)
        assert out == [{"start": 0.0, "title": "Intro [CHAPTER]"}]

    def test_unreadable_reply_gives_nothing(self):
        assert tc.parse_reply("sorry, no", 100) == [] and tc.parse_reply('{"chapters": 3}', 100) == []

    def test_timestamp_list_is_youtube_shaped(self):
        assert tc.timestamp_list([{"start": 0, "title": "Intro"}, {"start": 3725, "title": "Game"}]) == \
            "0:00 Intro\n1:02:05 Game\n"

    def test_a_long_stream_is_sampled_whole_not_cut(self):
        lines = [Line(idx=i, start=i * 10.0, end=i * 10.0 + 9, zh="x", en="word " * 30) for i in range(3000)]
        body = tc.transcript_for_prompt(lines)
        assert len(body) <= tc.TRANSCRIPT_MAX_CHARS and body.rstrip().splitlines()[-1].startswith("[")
        assert int(body.splitlines()[-1].split(":")[0].lstrip("[")) >= 7  # still reaches the last hours


def _title():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i * 60.0, end=i * 60.0 + 50, zh=f"句{i}", en=f"line {i}")
                        for i in range(20)])
    return did


class TestService:
    def test_generate_saves_and_get_returns_them(self, isolated_db, monkeypatch):
        from engine_backends import llm_tasks
        monkeypatch.setattr(llm_tasks, "generate_topic_chapters",
                            lambda lines, engine, duration, usage_cb=None: [{"start": 0.0, "title": "Hello"}])
        did = _title()
        out = topic_chapter_service.generate(did, "fake")
        assert out["chapters"] == [{"start": 0.0, "title": "Hello"}]
        assert topic_chapter_service.get(did)["chapters"] == out["chapters"]
        assert topic_chapter_service.timestamp_text(did) == "0:00 Hello\n"

    def test_empty_reply_keeps_the_old_chapters(self, isolated_db, monkeypatch):
        from engine_backends import llm_tasks
        did = _title()
        topic_chapter_service.save(did, [{"start": 0, "title": "Mine"}])
        monkeypatch.setattr(llm_tasks, "generate_topic_chapters", lambda *a, **k: [])
        with pytest.raises(ServiceError, match="returned no chapters"):
            topic_chapter_service.generate(did, "fake")
        assert topic_chapter_service.chapters_for(did) == [{"start": 0.0, "title": "Mine"}]

    def test_no_lines_and_no_chapters_are_refused(self, isolated_db):
        empty = db.create_drama(title_zh="E")
        with pytest.raises(UnsupportedOperationError):
            topic_chapter_service.generate(empty, "fake")
        with pytest.raises(InvalidInputError):
            topic_chapter_service.timestamp_text(_title())

    def test_save_cleans_and_empty_list_clears(self, isolated_db):
        did = _title()
        assert topic_chapter_service.save(did, [{"start": 300, "title": " B "}, {"start": 5, "title": "A"}]) == \
            [{"start": 0.0, "title": "A"}, {"start": 300.0, "title": "B"}]
        assert topic_chapter_service.save(did, []) == [] and topic_chapter_service.chapters_for(did) == []

    def test_routes(self, isolated_db, local_client, monkeypatch):
        from engine_backends import llm_tasks
        monkeypatch.setattr(llm_tasks, "generate_topic_chapters",
                            lambda lines, engine, duration, usage_cb=None: [{"start": 0.0, "title": "Hello"}])
        did = _title()
        base = f"/api/translate-run/dramas/{did}/topic-chapters"
        assert local_client.get(base).json()["chapters"] == []
        assert local_client.post(f"{base}/generate", json={"engine": "fake"}).json()["chapters"][0]["title"] == "Hello"
        r = local_client.get(f"/api/export/dramas/{did}/chapters-text")
        assert r.status_code == 200 and r.text == "0:00 Hello\n"
        assert local_client.post(base, json={"chapters": []}).json() == {"chapters": []}
        assert local_client.get(f"/api/export/dramas/{did}/chapters-text").status_code == 422


class TestMux:
    def test_chapters_file_is_a_third_input_and_replaces_source_chapters(self):
        cmd = ve.mux_soft_subtitles_cmd("v.mp4", "s.srt", "o.mp4", chapters_path="c.ffmeta")
        assert cmd[cmd.index("c.ffmeta") - 1] == "-i" and cmd[cmd.index("-map_chapters") + 1] == "2"

    def test_without_chapters_the_command_is_unchanged(self):
        assert "-map_chapters" not in ve.mux_soft_subtitles_cmd("v.mp4", "s.srt", "o.mp4")
