"""The comic reader's panel route: panels found on a stored page, in the title's reading order."""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

W, H = 1000, 1400
# Two rows of two bordered panels, so the reading order shows which side a row starts from.
BOXES = [(40, 40, 420, 620), (520, 40, 440, 620), (40, 720, 420, 640), (520, 720, 440, 640)]


def _page_png(path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((H, W, 3), 255, np.uint8)
    for x, y, w, h in BOXES:
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), 6)
        cv2.circle(img, (x + w // 2, y + h // 2), 60, (90, 90, 90), -1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    assert cv2.imwrite(path, img)


@pytest.fixture
def page(isolated_db):
    import db
    pytest.importorskip("cv2")
    from services import comic_panels_service
    comic_panels_service._cache.clear()

    def make(language):
        d = db.create_drama(title_en="Comic", media_type="manga", source_language=language)
        _page_png(os.path.join(db.drama_dir(d), "pages", "page_0000.png"))
        return d, db.create_page(d, 0, "pages/page_0000.png", W, H)
    return make


def _url(d, p, query=""):
    return f"/api/scanlate/dramas/{d}/pages/{p}/panels{query}"


def _starts(body):
    return [(round(b["x"] * W), round(b["y"] * H)) for b in body["panels"]]


def _near(found, expected):
    return all(abs(a - b) <= 10 for f, e in zip(found, expected) for a, b in zip(f, e)) and len(found) == len(expected)


def test_japanese_rows_read_from_the_right(client, page):
    d, p = page("ja")
    r = client.get(_url(d, p))
    assert r.status_code == 200
    assert _near(_starts(r.json()), [(520, 40), (40, 40), (520, 720), (40, 720)])


def test_chinese_and_korean_rows_read_from_the_left(client, page):
    for language in ("zh", "ko"):
        d, p = page(language)
        assert _near(_starts(client.get(_url(d, p)).json()), [(40, 40), (520, 40), (40, 720), (520, 720)])


def test_the_reader_direction_overrides_the_language(client, page):
    d, p = page("ja")
    assert _near(_starts(client.get(_url(d, p, "?rtl=false")).json()), [(40, 40), (520, 40), (40, 720), (520, 720)])


def test_boxes_are_page_fractions(client, page):
    d, p = page("zh")
    first = client.get(_url(d, p)).json()["panels"][0]
    assert set(first) == {"x", "y", "w", "h"}
    assert all(0 <= v <= 1 for v in first.values())


def test_a_page_without_panels_or_unreadable_is_empty(client, isolated_db):
    import db
    d = db.create_drama(title_en="Comic", media_type="manga", source_language="ja")
    path = os.path.join(db.drama_dir(d), "pages", "blank.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + bytes(64))
    p = db.create_page(d, 0, "pages/blank.png", W, H)
    r = client.get(_url(d, p))
    assert r.status_code == 200 and r.json() == {"page_id": p, "panels": []}


def test_a_strip_is_not_decoded(client, page, monkeypatch):
    import db
    from services import comic_panels_service
    d, p = page("ko")
    db.update_page(p, width=800, height=8000)
    monkeypatch.setattr(comic_panels_service, "_find_panels", lambda path: pytest.fail("decoded a strip"))
    assert client.get(_url(d, p)).json()["panels"] == []


def test_the_answer_is_kept_until_the_file_changes(client, page, monkeypatch):
    from services import comic_panels_service
    d, p = page("zh")
    calls = []
    real = comic_panels_service._find_panels
    monkeypatch.setattr(comic_panels_service, "_find_panels", lambda path: calls.append(path) or real(path))
    client.get(_url(d, p))
    client.get(_url(d, p, "?rtl=true"))
    assert len(calls) == 1
    import db
    path = os.path.join(db.drama_dir(d), "pages", "page_0000.png")
    _page_png(path)
    later = os.stat(path).st_mtime + 5
    os.utime(path, (later, later))
    assert len(client.get(_url(d, p)).json()["panels"]) == 4
    assert len(calls) == 2


def test_a_page_whose_file_is_too_large_is_never_decoded(client, page, monkeypatch):
    import db
    from scanlate import scanlate_page_decode
    from services import comic_panels_service
    d, p = page("zh")
    # The stored size says small; the file's header says otherwise.
    db.update_page(p, width=10, height=10)
    monkeypatch.setattr(comic_panels_service, "_MAX_PIXELS", W * H - 1)
    monkeypatch.setattr(scanlate_page_decode, "decode_page", lambda path: pytest.fail("decoded a page over the cap"))
    r = client.get(_url(d, p))
    assert r.status_code == 200 and r.json()["panels"] == []


def test_the_cache_keeps_only_the_newest_pages(tmp_path, monkeypatch):
    from services import comic_panels_service as svc
    monkeypatch.setattr(svc, "_CACHE_SIZE", 2)
    monkeypatch.setattr(svc, "_cache", type(svc._cache)())
    monkeypatch.setattr(svc, "_find_panels", lambda path: (10, 10, []))
    files = []
    for n in range(3):
        f = tmp_path / f"p{n}.png"
        f.write_bytes(b"x")
        files.append((str(f), os.stat(f)))
        svc._cached_panels(*files[-1])
    assert [k[0] for k in svc._cache] == [files[1][0], files[2][0]]


def test_a_missing_install_is_asked_again(tmp_path, monkeypatch):
    from services import comic_panels_service as svc
    monkeypatch.setattr(svc, "_cache", type(svc._cache)())
    answers = [svc._Unavailable(), (10, 10, [])]

    def find(path):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(svc, "_find_panels", find)
    f = tmp_path / "p.png"
    f.write_bytes(b"x")
    assert svc._cached_panels(str(f), os.stat(f)) is None
    assert svc._cached_panels(str(f), os.stat(f)) == (10, 10, [])


def test_a_page_opencv_cannot_handle_is_empty_not_an_error(client, page, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from scanlate import scanlate_panels

    def broken(*a, **k):
        raise cv2.error("bad page")
    monkeypatch.setattr(scanlate_panels, "detect_panels", broken)
    d, p = page("zh")
    r = client.get(_url(d, p))
    assert r.status_code == 200 and r.json()["panels"] == []


def test_another_titles_page_is_404(client, page):
    d, p = page("ja")
    other, _ = page("ja")
    assert client.get(_url(other, p)).status_code == 404
    assert client.get(_url(9999, p)).status_code == 404


def test_no_more_than_two_pages_are_decoded_at_once(tmp_path, monkeypatch):
    import threading
    import time
    from services import comic_panels_service as svc
    running, most, lock = [0], [0], threading.Lock()

    def slow(path):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return (10, 10, [])
    monkeypatch.setattr(svc, "_find_panels", slow)
    monkeypatch.setattr(svc, "_cache", type(svc._cache)())
    files = []
    for n in range(6):
        f = tmp_path / f"p{n}.png"
        f.write_bytes(b"x")
        files.append((str(f), os.stat(f)))
    threads = [threading.Thread(target=svc._cached_panels, args=pf) for pf in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 2
