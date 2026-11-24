"""The site's captions kept with a title: file choice, machine-caption repeats, and the hints they feed."""
import os

import db
from core import Line
from engine_backends import llm_tasks
from services import site_captions_service as sc
from services import transcribe_service, ytdlp_child

ROLLING = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n\n"
           "00:00:02.000 --> 00:00:03.000\nhello\nthere\n\n"
           "00:00:03.000 --> 00:00:04.000\nthere\nfriend\n")


def test_language_lists_and_options():
    assert ytdlp_child.caption_languages("ja")[0] == "ja" and ytdlp_child.caption_languages("fr") == ()
    opts = ytdlp_child.ydl_options("/t", lambda d: None, lambda i: None, captions=("ko",))
    assert opts["writesubtitles"] and opts["subtitleslangs"] == ["ko"]
    assert "writesubtitles" not in ytdlp_child.ydl_options("/t", lambda d: None, lambda i: None)


def test_pick_takes_the_earliest_wanted_language(tmp_path):
    for name in ("downloaded_audio.ja.vtt", "downloaded_audio.zh-Hans.vtt", "other.zh.vtt"):
        (tmp_path / name).write_text("WEBVTT\n")
    assert sc.pick(str(tmp_path), ("zh", "zh-Hans", "ja")).endswith("zh-Hans.vtt")
    assert sc.pick(str(tmp_path), ("ko",)) is None


def test_rolling_machine_captions_are_cleaned_and_saved(isolated_db, tmp_path):
    did = db.create_drama(title_zh="D")
    src = tmp_path / "downloaded_audio.en.vtt"
    src.write_text(ROLLING)
    assert sc.save(did, str(src)) is True
    assert sc.hint_text(did, 100) == "hello there friend"
    assert sc.hint_text(did, 5) == "hello"


def test_an_unreadable_file_saves_nothing(isolated_db, tmp_path):
    did = db.create_drama(title_zh="D")
    src = tmp_path / "downloaded_audio.en.vtt"
    src.write_text("not captions")
    assert sc.save(did, str(src)) is False and not sc.has_captions(did)


def test_the_hint_reaches_whisper_and_the_brief(isolated_db, tmp_path, monkeypatch):
    did = db.create_drama(title_zh="D")
    src = tmp_path / "downloaded_audio.en.vtt"
    src.write_text(ROLLING)
    sc.save(did, str(src))
    assert "hello there friend" in transcribe_service.build_auto_initial_prompt(did)

    sent = {}
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda engine, prompt, **kw: sent.update(p=prompt))

    class Engine:
        supports_reference = True
        client = object()
    llm_tasks.generate_episode_brief([Line(idx=0, start=0, end=1, zh="你好")], Engine(),
                                     {"site_captions": "hello there friend"})
    assert "Reference only" in sent["p"] and "hello there friend" in sent["p"]
    assert os.path.basename(db.drama_dir(did))
