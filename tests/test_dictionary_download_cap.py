"""The CC-CEDICT download is capped, packed and unpacked."""
import gzip
import io

import pytest

import dictionary


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _install(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(dictionary, "CEDICT_PATH", str(tmp_path / "cedict.txt"))
    monkeypatch.setattr(dictionary.urllib.request, "urlopen",
                        lambda url, timeout=None: _Resp(payload))


def test_an_oversized_download_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(dictionary, "CEDICT_MAX_DOWNLOAD_BYTES", 100)
    _install(monkeypatch, tmp_path, b"x" * 500)
    with pytest.raises(RuntimeError, match="larger than expected"):
        dictionary._ensure_cedict()
    assert not (tmp_path / "cedict.txt").exists()


def test_a_gzip_bomb_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(dictionary, "CEDICT_MAX_UNPACKED_BYTES", 1000)
    _install(monkeypatch, tmp_path, gzip.compress(b"0" * 100_000))
    with pytest.raises(RuntimeError, match="larger than expected"):
        dictionary._ensure_cedict()
    assert not (tmp_path / "cedict.txt").exists()


def test_a_normal_download_is_written(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, gzip.compress(b"# cedict\n"))
    dictionary._ensure_cedict()
    assert (tmp_path / "cedict.txt").read_bytes() == b"# cedict\n"
