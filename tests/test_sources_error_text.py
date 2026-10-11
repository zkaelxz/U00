"""Adapter error text is fixed wording: a hostile page must never reach it."""
import base64

import pytest

EVIL = "EVIL_MARKER"


def _assert_clean(exc_info):
    assert EVIL not in str(exc_info.value)


def test_kuaikan_unknown_name_in_state_is_not_echoed():
    from sources.adapters import kuaikan
    html = f"<script>window.__NUXT__=(function(a){{return {{k:{EVIL}}}}}(1));</script>"
    with pytest.raises(kuaikan.LayoutChanged) as e:
        kuaikan._extract_nuxt_state(html)
    _assert_clean(e)


def _raises_evil(*_a, **_k):
    raise ValueError(EVIL)


# Decoder exceptions (json, base64, UTF-8) can carry bytes of the page, so the
# adapters must never build their message from them.
def test_manhuagui_json_error_is_not_echoed(monkeypatch):
    from sources.adapters import manhuagui
    html = "window[\"\\x65\\x76\\x61\\x6c\"](function(p,a,c,k,e,d){return p}('{x}',0,0,''.split('|'),0,{}))"
    monkeypatch.setattr(manhuagui.json, "loads", _raises_evil)
    with pytest.raises(manhuagui.LayoutChanged) as e:
        manhuagui.decode_image_data(html)
    _assert_clean(e)


def test_miaoqumh_payload_errors_are_not_echoed(monkeypatch):
    from sources.adapters import miaoqumh
    monkeypatch.setattr(miaoqumh.base64, "b64decode", _raises_evil)
    with pytest.raises(miaoqumh.LayoutChanged) as e:
        miaoqumh.decode_page_data("var DATA = 'abcd'", 1)
    _assert_clean(e)
    monkeypatch.undo()
    key = miaoqumh._XOR_KEYS[1].encode("ascii")
    xored = bytes(b ^ key[i % 8] for i, b in enumerate(b"AAAA"))
    monkeypatch.setattr(miaoqumh.json, "loads", _raises_evil)
    with pytest.raises(miaoqumh.LayoutChanged) as e:
        miaoqumh.decode_page_data("var DATA = '" + base64.b64encode(xored).decode() + "'", 1)
    _assert_clean(e)


def test_toonkor_payload_error_is_not_echoed(monkeypatch):
    from sources.adapters import toonkor

    def boom(_):
        raise ValueError(EVIL)
    monkeypatch.setattr(base64, "b64decode", boom)
    with pytest.raises(toonkor.LayoutChanged) as e:
        toonkor.decode_toon_img("var toon_img = 'abc'")
    _assert_clean(e)


def test_ranobes_data_blob_error_is_not_echoed(monkeypatch):
    from sources.adapters import ranobes
    monkeypatch.setattr(ranobes.json, "loads", _raises_evil)
    with pytest.raises(ranobes.LayoutChanged) as e:
        ranobes._extract_data_blob("window.__DATA__ = {}")
    _assert_clean(e)


def test_mangak_slug_is_not_echoed():
    from sources.adapters import mangak
    with pytest.raises(mangak.LayoutChanged) as e:
        mangak._safe_slug(EVIL + " /bad", "chapter id")
    _assert_clean(e)


def test_baozimh_decode_errors_are_not_echoed(monkeypatch):
    from sources.adapters import baozimh
    dec = baozimh.ChapterImageDecoder
    a, b, c = 3, 3, 3
    data = dec.PREFIX + "p" * a + dec.MARKER1 + "q" * b + dec.MARKER2 + "r" * c + dec.SUFFIX

    class Table(dict):
        def __getitem__(self, key):
            raise KeyError(EVIL)
    monkeypatch.setattr(dec, "_DECODE_TABLE", Table())
    with pytest.raises(baozimh.LayoutChanged) as e:
        dec.decode(data)
    _assert_clean(e)
    monkeypatch.undo()
    monkeypatch.setattr(baozimh.base64, "urlsafe_b64decode", _raises_evil)
    with pytest.raises(baozimh.LayoutChanged) as e:
        dec.decode(data)
    _assert_clean(e)
