"""The CC-CEDICT download is capped, packed and unpacked."""
import gzip
import io

import pytest

import dictionary
from lib import http


class _Resp:
    status_code = 200
    headers = {}
    encoding = None

    def __init__(self, payload):
        self._buf = io.BytesIO(payload)

    def iter_content(self, size):
        while chunk := self._buf.read(size):
            yield chunk

    def close(self):
        self._buf.close()


def _install(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(dictionary, "CEDICT_PATH", str(tmp_path / "cedict.txt"))
    monkeypatch.setattr(http, "pinned_get", lambda *a, **kw: _Resp(payload))


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


def test_a_slow_drip_download_is_stopped_by_the_total_deadline(monkeypatch, tmp_path):
    # A negative budget is already spent when the first chunk arrives.
    monkeypatch.setattr(dictionary, "CEDICT_DOWNLOAD_DEADLINE_SECONDS", -1)
    _install(monkeypatch, tmp_path, gzip.compress(b"# cedict\n"))
    with pytest.raises(RuntimeError, match="took too long"):
        dictionary._ensure_cedict()
    assert not (tmp_path / "cedict.txt").exists()


def test_an_error_page_is_not_treated_as_the_archive(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, b"<html>404</html>")
    monkeypatch.setattr(http, "pinned_get", lambda *a, **kw: _ErrResp(b"<html>404</html>"))
    with pytest.raises(RuntimeError, match="^CEDICT download failed$"):
        dictionary._ensure_cedict()
    assert not (tmp_path / "cedict.txt").exists()


class _ErrResp(_Resp):
    status_code = 404


def test_a_network_failure_gets_the_dictionary_message(monkeypatch, tmp_path):
    monkeypatch.setattr(dictionary, "CEDICT_PATH", str(tmp_path / "cedict.txt"))

    def down(*a, **kw):
        raise http.FetchError()

    monkeypatch.setattr(http, "pinned_get", down)
    with pytest.raises(RuntimeError, match="CC-CEDICT download failed"):
        dictionary._ensure_cedict()
