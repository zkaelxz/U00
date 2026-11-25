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


def test_no_more_than_two_pages_are_decoded_at_once(client, monkeypatch):
    import threading
    import time
    from services import comic_reader_copy_service as svc
    live, peak, lock = 0, 0, threading.Lock()
    real = svc._write_copy

    def slow(*a):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        real(*a)
        with lock:
            live -= 1

    monkeypatch.setattr(svc, "_write_copy", slow)
    pages = [_page(_png(seed=i)) for i in range(5)]
    threads = [threading.Thread(target=svc.reader_copy, args=(d, {
        "path": path, "stat": os.stat(path), "content_type": "image/png", "ordinal": 1}))
        for d, _, path in pages]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert 1 <= peak <= 2


def test_the_low_data_copy_is_narrower_and_smaller_and_keeps_the_shape(client):
    d, p, _ = _page(_png(size=(1000, 1500)))
    reader = client.get(_url(d, p))
    low = client.get(_url(d, p, "low"))
    assert low.status_code == 200 and low.headers["content-type"] == "image/jpeg"
    w, h = Image.open(io.BytesIO(low.content)).size
    assert w == 720 and abs(h / w - 1.5) < 0.01
    assert len(low.content) < len(reader.content)


def test_both_copies_of_a_page_stay_and_a_rewrite_drops_both(client):
    d, p, path = _page(_png(1))
    client.get(_url(d, p))
    client.get(_url(d, p, "low"))
    cache = os.path.join(db.drama_dir(d), "reader_cache")
    assert len(os.listdir(cache)) == 2
    with open(path, "wb") as f:
        f.write(_png(2))
    client.get(_url(d, p, "low"))
    assert [n.endswith(".low.jpg") for n in os.listdir(cache)] == [True]
