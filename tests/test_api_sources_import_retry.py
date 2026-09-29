"""Step 107: partial-import retry state. A chapter import records which
chapters failed (and why, redacted) and which were never attempted after a
browser check / terms stop / cancel; GET /api/sources/{name}/import-state
returns those plus the chapters already imported, so the picker can mark
them and retry only the failed ones. Fake adapters, no network."""
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import db
from sources import store
from sources.models import (ChallengeDetected, FailureReason, SourceUnavailable,
                            TermsProhibited)

from tests.test_api_sources_import import (HOST, SECRET, _make, _novel, _result,  # noqa: F401
                                           _wait, client, fakes)


def _start(client, did, ids, name="alpha"):
    r = client.post(f"/api/sources/{name}/import",
                    json={"series_id": "s1", "chapter_ids": ids, "drama_id": did})
    assert r.status_code == 200, r.text
    return _result(client, f"sourceimport_{did}")[1]["result"]


def _state(client, did, name="alpha", series_id="s1"):
    return client.get(f"/api/sources/{name}/import-state",
                      params={"series_id": series_id, "drama_id": did})


def test_failed_chapter_manifest_and_retry_only_those(client, fakes):
    fail = {"c2": SourceUnavailable(f"down {SECRET} C:\\x\\y")}
    calls = []
    fakes["alpha"] = _make("alpha", fail=fail, calls=calls)
    did = _novel()
    res = _start(client, did, ["c1", "c2", "c10"])
    assert res["retry_chapter_ids"] == ["c2"] and res["not_attempted_count"] == 0

    r = _state(client, did)
    assert r.status_code == 200 and SECRET not in r.text and "C:\\" not in r.text
    body = r.json()
    assert body["imported_chapter_ids"] == ["c1", "c10"]
    assert body["retry_count"] == 1
    row = body["retry"][0]
    assert row["chapter_id"] == "c2" and row["status"] == "failed" and "down" in row["error"]
    # the stored manifest is redacted too
    assert SECRET not in str(store.import_retry_rows("alpha", "s1", did))

    # the retry fetches only c2; once it works the manifest is empty
    fail.clear()
    calls.clear()
    res = _start(client, did, [x["chapter_id"] for x in body["retry"]])
    assert calls == ["c2"] and res["retry_chapter_ids"] == []
    after = _state(client, did).json()
    assert after["retry"] == [] and after["imported_chapter_ids"] == ["c1", "c10", "c2"]


@pytest.mark.parametrize("stop", [
    ChallengeDetected("challenge", f"{HOST}/v?cf={SECRET}", FailureReason.CLOUDFLARE_CHALLENGE),
    TermsProhibited(f"terms {SECRET}"),
])
def test_stop_records_remaining_chapters_as_not_attempted(client, fakes, stop):
    calls = []
    fakes["alpha"] = _make("alpha", fail={"c1": stop}, calls=calls)
    did = _novel()
    res = _start(client, did, ["c1", "c2", "c10"])
    # the job still stops at the first chapter (by design) ...
    assert calls == ["c1"]
    outcomes = {c["chapter_id"]: c["outcome"] for c in res["chapters"]}
    assert outcomes == {"c1": "failed", "c2": "not_attempted", "c10": "not_attempted"}
    assert res["not_attempted_count"] == 2 and res["partial"] is True
    assert res["retry_chapter_ids"] == ["c1", "c2", "c10"]
    # ... and every chapter it didn't finish is in the manifest
    body = _state(client, did).json()
    assert [(x["chapter_id"], x["status"]) for x in body["retry"]] == [
        ("c1", "failed"), ("c2", "not_attempted"), ("c10", "not_attempted")]
    assert SECRET not in str(body)


def test_cancel_records_unreached_chapters(client, fakes):
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", gate=gate)
    did = _novel()
    jid = f"sourceimport_{did}"
    client.post("/api/sources/alpha/import",
                json={"series_id": "s1", "chapter_ids": ["c1", "c2"], "drama_id": did})
    client.post(f"/api/jobs/{jid}/cancel", headers={"X-Baihe-Local": "1"})
    gate.set()
    res = _result(client, jid)[1]["result"]
    assert res["cancelled"] is True
    # c1 was interrupted mid-fetch and c2 never started: neither finished
    assert {c["chapter_id"] for c in res["chapters"] if c["outcome"] == "not_attempted"} == \
        {"c1", "c2"}
    assert [x["chapter_id"] for x in _state(client, did).json()["retry"]] == ["c1", "c2"]


def test_imported_elsewhere_leaves_the_manifest(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable("down")})
    did = _novel()
    _start(client, did, ["c2"])
    assert _state(client, did).json()["retry_count"] == 1
    store.record_imported("alpha", "s1", "c2", did)   # e.g. the tracked-series auto-import
    body = _state(client, did).json()
    assert body["retry_count"] == 0 and body["imported_chapter_ids"] == ["c2"]


def test_manifest_is_per_drama_and_series(client, fakes):
    fakes["alpha"] = _make("alpha", fail={"c2": SourceUnavailable("down")})
    did, other = _novel(), _novel()
    _start(client, did, ["c2"])
    assert _state(client, other).json()["retry"] == []
    assert _state(client, did, series_id="s2").json()["retry"] == []


def test_import_state_validation(client, fakes):
    fakes["alpha"] = _make("alpha")
    did = _novel()
    assert _state(client, did).status_code == 200
    assert _state(client, did, name="nope").status_code == 404
    assert _state(client, 99999).status_code == 404
    assert _state(client, did, series_id="http://evil/x").status_code == 422
    assert _state(client, did, series_id="//evil/x").status_code == 422
    assert client.get("/api/sources/alpha/import-state",
                      params={"series_id": "s1", "drama_id": 0}).status_code == 422
    assert client.get("/api/sources/alpha/import-state",
                      params={"series_id": "s1"}).status_code == 422
    assert db.get_drama(did) is not None
    assert background_jobs.get_status(f"sourceimport_{did}") is None
