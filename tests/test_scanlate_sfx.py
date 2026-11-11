"""A translated sound effect is glossed beside the art, never over it, and only when asked for."""

import pytest

pytest.importorskip("PIL")
from PIL import Image

import scanlate_sfx
from services import scanlate_run_service as run_svc
from tests.saved_settings import patch_setting


def _sfx(**kw):
    return {"x": 100, "y": 100, "w": 80, "h": 60, "kind": "sfx", "skip": False,
            "source_text": "털썩", "translated_text": "thud", **kw}


def test_only_a_translated_sound_effect_left_as_it_is_is_glossed():
    assert scanlate_sfx.is_glossed(_sfx())
    assert not scanlate_sfx.is_glossed(_sfx(translated_text=""))
    assert not scanlate_sfx.is_glossed(_sfx(skip=True))
    assert not scanlate_sfx.is_glossed(_sfx(include_sfx=True))      # replaced like a bubble instead
    assert not scanlate_sfx.is_glossed(_sfx(kind="bubble"))


def test_the_gloss_lands_beside_the_effect_and_leaves_its_lettering_alone():
    page = Image.new("RGB", (400, 300), (90, 90, 90))
    region = _sfx()
    scanlate_sfx.draw_gloss(page, region)
    pixels = page.load()
    inside = [pixels[x, y] for x in range(100, 180) for y in range(100, 160)]
    assert set(inside) == {(90, 90, 90)}
    assert any(pixels[x, y] != (90, 90, 90) for x in range(180, 400) for y in range(95, 165))


def test_a_gloss_at_the_page_edge_stays_on_the_page():
    page = Image.new("RGB", (200, 120), "white")
    scanlate_sfx.draw_gloss(page, _sfx(x=130, y=70, w=70, h=50, translated_text="thump thump"))
    assert page.getbbox() is not None and page.getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("on, sent", [(False, []), (True, ["0"])])
def test_sound_effects_join_the_translation_request_only_with_the_setting(monkeypatch, on, sent):
    patch_setting(monkeypatch, "scanlate_sfx_gloss", on)
    asked = {}

    def by_id(keyed, engine, drama, **kw):
        asked.update(keyed)
        return {k: "thud" for k in keyed}, None

    import scanlate_translate
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", by_id)
    region = _sfx(translated_text="")
    ok, _ = run_svc._translate([region], object(), "claude", {}, None, "", 1, [])
    assert ok and list(asked) == sent
    assert region["translated_text"] == ("thud" if on else "")
