"""decode_html: declared charsets stay authoritative; pages that declare
nothing and are not UTF-8 are sniffed among the CJK legacy encodings."""
import pytest

from sources.http import Response, decode_html

ZH = "第一章 重生之后，他站在雨中，看着远方的城市慢慢亮起灯火。"
ZH_TW = "第一章 重生之後，他站在雨中，看著遠方的城市慢慢亮起燈火。"
JA = "第一章 彼は雨の中に立ち、遠くの街に明かりがともるのを見ていた。"
KO = "제1장 그는 빗속에 서서 멀리 도시에 불이 켜지는 것을 바라보았다."
EN = "<html><head><title>Chapter 1</title></head><body><p>Hello, world.</p></body></html>"


def page(text):
    return f"<html><body><p>{text}</p></body></html>"


@pytest.mark.parametrize("text,enc", [
    (ZH, "gbk"), (ZH, "gb18030"), (ZH_TW, "big5"), (JA, "shift_jis"), (JA, "cp932"),
    (JA, "euc_jp"), (KO, "euc_kr"),
])
def test_undeclared_legacy_encoding_is_detected(text, enc):
    assert decode_html(page(text).encode(enc)) == page(text)


def test_undeclared_with_unrelated_header():
    h = {"Content-Type": "text/html"}
    assert decode_html(page(ZH).encode("gbk"), h) == page(ZH)


def test_utf8_with_and_without_bom():
    raw = page(ZH).encode("utf-8")
    assert decode_html(raw) == page(ZH)
    assert decode_html(b"\xef\xbb\xbf" + raw) == page(ZH)
    assert decode_html(b"\xef\xbb\xbf" + raw, {"Content-Type": "text/html; charset=utf-8"}) == page(ZH)


def test_declared_gb2312_uses_gb18030():
    rare = "㐀"   # outside GB2312/GBK, inside GB18030
    raw = page(ZH + rare).encode("gb18030")
    got = decode_html(raw, {"Content-Type": "text/html; charset=gb2312"})
    assert got == page(ZH + rare)
    meta = b'<meta charset="gbk">' + ZH.encode("gb18030") + rare.encode("gb18030")
    assert decode_html(meta).endswith(ZH + rare)


def test_declared_charset_wins_over_sniffing():
    raw = ('<meta charset="shift_jis">' + JA).encode("shift_jis")
    assert decode_html(raw).endswith(JA)
    # A valid declaration is not overridden even when the bytes suit another one.
    raw = page(ZH).encode("gbk")
    assert decode_html(raw, {"Content-Type": "text/html; charset=latin-1"}) == raw.decode("latin-1")
    # An unknown declared name falls back to sniffing.
    assert decode_html(page(ZH).encode("gbk"), {"Content-Type": "text/html; charset=nonsense-9"}) == page(ZH)


def test_garbage_never_raises():
    import os
    for raw in (b"", b"\xff\xfe\xfd\x00\x01", bytes(range(256)) * 50, os.urandom(3000),
                b"\x81", b"<meta charset=", ZH.encode("gbk")[:-1]):
        assert isinstance(decode_html(raw), str)
        assert isinstance(decode_html(raw, {"Content-Type": "text/html; charset=big5"}), str)
    assert decode_html(None) == ""
    assert decode_html(b"abc", {"Content-Type": None}) == "abc"


def test_mixed_language_text():
    text = page(ZH + " Chapter 2: The Rain. 第二章 " + "ABC123")
    assert decode_html(text.encode("gbk")) == text
    ja = page(JA + " Chapter 2 ok")
    assert decode_html(ja.encode("shift_jis")) == ja


def test_ascii_pages_unchanged():
    assert decode_html(EN.encode()) == EN
    assert decode_html(EN.encode(), {"Content-Type": "text/html"}) == EN
    assert decode_html(b"\x93 caf\xe9 \x94") is not None


def test_large_page_truncated_sample_boundary():
    text = page(ZH * 5000)
    raw = text.encode("gbk")
    assert len(raw) > 64 * 1024
    assert decode_html(raw) == text


def test_response_text_uses_decoder():
    r = Response(200, {}, page(ZH).encode("gbk"), "https://example.com/")
    assert r.text == page(ZH)
