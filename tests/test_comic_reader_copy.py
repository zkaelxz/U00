"""The comic viewer's smaller page copy (?size=reader): real Pillow images in an
isolated library, no network."""

import io
import os
import random

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
PIL = pytest.importorskip("PIL")
from PIL import Image

import db


def _png(seed=1, size=(400, 600)):
    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    img.putdata([(rnd.randrange(256),) * 3 for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _page(data, name="pages/page_0000.png"):
    d = db.create_drama(title_en="Comic", media_type="manga")
    path = os.path.join(db.drama_dir(d), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return d, db.create_page(d, 0, name, 400, 600), path


def _url(d, p, size="reader"):
    return f"/api/scanlate/dramas/{d}/pages/{p}/image?variant=original&size={size}"


def test_the_reader_copy_is_a_smaller_jpeg_of_the_same_size(client):
    data = _png()
    d, p, _ = _page(data)
    r = client.get(_url(d, p))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert len(r.content) < len(data)
    assert Image.open(io.BytesIO(r.content)).size == (400, 600)


def test_the_full_size_is_still_the_original_file(client):
    data = _png()
    d, p, _ = _page(data)
    r = client.get(_url(d, p, "full"))
    assert r.headers["content-type"] == "image/png" and r.content == data


def test_the_copy_is_made_once(client):
    d, p, _ = _page(_png())
    client.get(_url(d, p))
    cache = os.path.join(db.drama_dir(d), "reader_cache")
    copy = os.path.join(cache, os.listdir(cache)[0])
    stamp = os.stat(copy).st_mtime_ns
    client.get(_url(d, p))
    assert os.listdir(cache) == [os.path.basename(copy)] and os.stat(copy).st_mtime_ns == stamp


def test_a_page_rewritten_in_place_gets_a_new_copy_and_drops_the_old(client):
    d, p, path = _page(_png(1))
    first = client.get(_url(d, p)).content
    with open(path, "wb") as f:
        f.write(_png(2))
    assert client.get(_url(d, p)).content != first
    assert len(os.listdir(os.path.join(db.drama_dir(d), "reader_cache"))) == 1


def test_a_page_that_cannot_be_decoded_is_served_as_it_is(client):
    junk = b"\x89PNG\r\n\x1a\n" + bytes(range(200))
    d, p, _ = _page(junk)
    r = client.get(_url(d, p))
    assert r.status_code == 200 and r.content == junk


def test_the_reader_size_must_be_known(client):
    d, p, _ = _page(_png())
    assert client.get(_url(d, p, "huge")).status_code == 422
