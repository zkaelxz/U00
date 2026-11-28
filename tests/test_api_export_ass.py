"""Tests for Migration Slice 27's ASS export endpoints (api/routers/export_routes.py)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


from core import Line


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    return did


def _url(did):
    return f"/api/export/dramas/{did}/ass"


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


def _style_line(text):
    return next(ln for ln in text.splitlines() if ln.startswith("Style:"))


def test_post_returns_ass_download(client, drama):
    r = client.post(_url(drama), json={})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-ssa")
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"D - subtitles (en).ass\"; filename*=UTF-8''D%20-%20subtitles%20%28en%29.ass")
    assert r.text.startswith("[Script Info]")
    assert "Hello" in r.text


@pytest.mark.parametrize("field,present,absent", [
    ("en", "Hello", "你好"), ("zh", "你好", "Hello"), ("bilingual", "Hello", None)])
def test_fields(client, drama, field, present, absent):
    r = client.post(_url(drama), json={"field": field})
    assert r.status_code == 200
    assert present in r.text
    if absent:
        assert absent not in r.text
    else:
        assert "你好" in r.text
    code = {"en": "en", "zh": "zh", "bilingual": "zh+en"}[field]
    what = "bilingual subtitles" if field == "bilingual" else "subtitles"
    assert f'filename="D - {what} ({code}).ass"' in r.headers["content-disposition"]


def test_style_override_applied_and_unset_falls_back_to_preset(client, drama):
    r = client.post(_url(drama), json={"style": {"font": "Meiryo", "size": 40}})
    assert r.status_code == 200
    line = _style_line(r.text)
    assert "Meiryo" in line and ",40," in line
    # Unset fields keep the Clean preset (outline_width 2, not None/overridden).
    base = _style_line(client.post(_url(drama), json={}).text)
    assert "Arial" in base and ",24," in base
    assert line.replace("Meiryo", "Arial").replace(",40,", ",24,") == base


@pytest.mark.parametrize("body", [
    {"style": {"primary": "red"}},
    {"style": {"outline": "#12345"}},
    {"speaker_colors": {"A": "nope"}},
    {"preset": "Nonexistent"},
    {"style": {"colour": "#FFFFFF"}},
    {"notes_as_separate_line": True},
    {"style": {"size": 500}},
    {"style": {"size": 1}},
    {"style": {"alignment": "middle"}},
    {"field": "fr"},
    {"wrap_chars_en": -1},
    {"wrap_chars_en": 201},
    {"wrap_chars_source": 201},
    {"style": {"font": "Arial\n[Events]"}},
    {"speaker_colors": {f"S{i}": "#123456" for i in range(201)}},
    {"speaker_colors": {"x" * 101: "#123456"}},
])
def test_invalid_requests_422(client, drama, body):
    r = client.post(_url(drama), json=body)
    assert r.status_code == 422
    _error(r)


def test_rejected_value_not_echoed_and_no_path(client, drama):
    r = client.post(_url(drama), json={"style": {"primary": "SECRETVALUE"}})
    assert r.status_code == 422
    assert "SECRETVALUE" not in r.text
    assert "/" not in _error(r)["message"].replace("#RRGGBB", "")
    r = client.post(_url(drama), json={"preset": "SECRETPRESET"})
    assert "SECRETPRESET" not in r.text


def test_unknown_drama_404(client, isolated_db):
    r = client.post(_url(9999), json={})
    assert r.status_code == 404
    _error(r)


def test_style_options(client):
    r = client.get("/api/export/ass-style-options")
    assert r.status_code == 200
    body = r.json()
    assert "Clean" in body["presets"]
    assert body["default_preset"] == "Clean"
    assert "Arial" in body["fonts"]
    assert body["alignments"]["bottom-center"] == 2
    assert body["size_range"] == [12, 60]
    assert body["custom_font_allowed"] is True


def _dialogues(text):
    return [ln.split(",", 9)[9] for ln in text.splitlines() if ln.startswith("Dialogue:")]


def _timed_drama(isolated_db, **fields):
    from segment_splitting import encode_line_words
    did = isolated_db.create_drama(title_en="D", **fields)
    words = [{"word": "你好，", "start": 0.2, "end": 0.6}, {"word": "世界", "start": 0.6, "end": 1.0}]
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="你好，世界", en="Hello, world",
             word_timings=encode_line_words("你好，世界", words)),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    return did


def test_karaoke_fills_each_word_as_it_is_spoken(client, isolated_db):
    did = _timed_drama(isolated_db)
    text = client.post(_url(did), json={"field": "zh", "karaoke": True}).text
    timed, untimed = _dialogues(text)
    assert timed == r"{\2a&H90&}{\k20}{\kf40}你好，{\kf40}世界{\r}"
    assert untimed == "再见"


def test_karaoke_leaves_the_english_alone(client, isolated_db):
    did = _timed_drama(isolated_db)
    both = _dialogues(client.post(_url(did), json={"field": "bilingual", "karaoke": True}).text)[0]
    assert both.startswith(r"Hello, world\N{\2a&H90&}")
    english = _dialogues(client.post(_url(did), json={"field": "en", "karaoke": True}).text)[0]
    assert english == "Hello, world"


def test_karaoke_follows_rows_broken_for_the_screen(client, isolated_db):
    did = _timed_drama(isolated_db)
    text = client.post(_url(did), json={"field": "zh", "karaoke": True, "wrap_chars_source": 3}).text
    assert _dialogues(text)[0] == r"{\2a&H90&}{\k20}{\kf40}你好，\N{\kf40}世界{\r}"


def test_reading_line_sits_small_above_the_original(client, isolated_db, monkeypatch):
    import segment
    monkeypatch.setattr(segment, "missing_reader_packages", lambda language: [])
    monkeypatch.setattr(segment, "reading_text", lambda text, language, chinese_script="simplified":
                        {"你好，世界": "nǐ hǎo, shì jiè"}.get(text, ""))
    did = _timed_drama(isolated_db)
    first, second = _dialogues(client.post(_url(did), json={"field": "bilingual", "reading_line": True}).text)
    assert first == r"Hello, world\N{\fscx60\fscy60}nǐ hǎo, shì jiè{\r}\N你好，世界"
    assert second == r"Bye\N再见"


def test_reading_line_without_its_packages_names_what_to_install(client, isolated_db, monkeypatch):
    import segment
    monkeypatch.setattr(segment, "missing_reader_packages", lambda language: ["pypinyin"])
    did = _timed_drama(isolated_db)
    r = client.post(_url(did), json={"field": "zh", "reading_line": True})
    assert r.status_code == 503
    assert "pypinyin" in _error(r)["message"]
    assert client.post(_url(did), json={"field": "en", "reading_line": True}).status_code == 200
