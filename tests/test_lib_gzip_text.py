import gzip

import pytest

from lib import gzip_text


def test_round_trip_through_pack_and_unpack():
    text = "第一行\nこんにちは\n안녕하세요"
    packed = gzip_text.pack(text)
    assert gzip_text.is_packed(packed)
    assert gzip_text.unpack(packed) == text


def test_the_same_input_always_packs_to_the_same_bytes():
    assert gzip_text.pack("same") == gzip_text.pack("same")


def test_plain_text_none_and_unpacked_bytes_pass_through_unpack():
    assert gzip_text.unpack(None) is None
    assert gzip_text.unpack("plain") == "plain"
    assert gzip_text.unpack("字".encode("utf-8")) == "字"


def test_truncated_data_is_rejected():
    packed = gzip_text.pack("x" * 10_000)
    with pytest.raises(ValueError, match="truncated"):
        gzip_text.gunzip_bytes(packed[:-10])


def test_damaged_data_is_rejected():
    packed = bytearray(gzip_text.pack("x" * 10_000))
    packed[len(packed) // 2] ^= 0xFF
    with pytest.raises(ValueError):
        gzip_text.gunzip_bytes(bytes(packed))
    with pytest.raises(ValueError, match="damaged"):
        gzip_text.gunzip_bytes(gzip_text.GZIP_MAGIC + b"not gzip at all")


def test_output_exactly_at_the_limit_is_accepted_and_one_byte_over_is_not():
    data = b"a" * 1000
    packed = gzip.compress(data)
    assert gzip_text.gunzip_bytes(packed, max_bytes=1000) == data
    with pytest.raises(ValueError, match="limit"):
        gzip_text.gunzip_bytes(packed, max_bytes=999)
    with pytest.raises(ValueError, match="limit"):
        gzip_text.unpack(packed, max_bytes=999)
