"""A Quick read page is remembered per look: the same style finds it again, another style does not."""

from services import extension_bridge_service as svc


def _key(style):
    job = {"drama_id": 0, "source_language": "ko", "typeset": True, "typeset_style": style,
           "typeset_max_width": 1080, "glossary_version": "g"}
    return svc._key_settings(job, {"engine": "e", "model": "m"})


def test_the_same_style_gives_the_same_key_each_time(isolated_db):
    first = _key(svc._typeset_style({"min_font": 22, "stroke": True, "font": "bangers"}))
    again = _key(svc._typeset_style({"min_font": 22, "stroke": True, "font": "bangers"}))
    assert first == again


def test_a_different_style_or_none_gives_another_key(isolated_db):
    base = _key(svc._typeset_style({"min_font": 22}))
    assert _key(svc._typeset_style({"min_font": 30})) != base
    assert _key(svc._typeset_style({"min_font": 22, "stroke": True})) != base
    assert _key(None) != base
