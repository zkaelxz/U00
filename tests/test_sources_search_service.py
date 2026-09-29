"""services/sources_search_service.py (spec S-3): search and series jobs,
error mapping, redaction and the known-chapters helper. Adapters are fakes
served by a scripted transport through the real SourceClient; no network."""

import json
import time

import pytest

import background_jobs
from services import sources_search_service as svc
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from sources import registry
from sources.base import SourceAdapter
from sources.http import (PacingPolicy, ResponseRefused, ResponseTooLarge, ResponseTooSlow,
                          UnsupportedEncoding)
from sources.models import (ChallengeDetected, ChapterInfo, ContentHidden, FailureReason,
                            NotSupportedError, SearchResult, SeriesInfo, SourceUnavailable,
                            TermsProhibited)
from tests.sources_helpers import ScriptedTransport, html

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
HOST = "https://fake.invalid"


def _make(name, routes=None, search_exc=None, series_exc=None, on_request=None):
    transport = ScriptedTransport(routes or {})
    if on_request:
        inner = transport

        def transport(method, url, headers, data, timeout):  # noqa: F811
            on_request(url)
            return inner(method, url, headers, data, timeout)

    class Fake(SourceAdapter):
        pass

    Fake.name = name
    Fake.display_name = name.title()

    def __init__(self, client=None, **kw):
        kw.setdefault("transport", transport)
        kw["policy"] = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
        SourceAdapter.__init__(self, client, **kw)

    def search(self, query, page=1):
        if search_exc:
            raise search_exc
        out = []
        for i in (1, 2):
            self.client.get(f"{HOST}/{name}/search/{i}?q={query}&token={SECRET}")
            out.append(SearchResult(name, f"s{i}", f"{name} title {i}",
                                    f"{HOST}/{name}/series/{i}?sig={SECRET}#frag"))
        return out

    def get_series(self, series_id):
        if series_exc:
            raise series_exc
        return SeriesInfo(name, series_id, "Series T", f"{HOST}/series/{series_id}?t={SECRET}",
                          description=f"see C:\\Users\\kae\\lib and key {SECRET}")

    def get_chapters(self, series_id):
        return [ChapterInfo(name, series_id, "c10", "第10话", f"{HOST}/c/10?x=1"),
                ChapterInfo(name, series_id, "c2", "第2话", f"{HOST}/c/2?x=1"),
                ChapterInfo(name, series_id, "c1", "第1话", f"{HOST}/c/1?x=1")]

    Fake.__init__ = __init__
    Fake.search = search
    Fake.get_series = get_series
    Fake.get_chapters = get_chapters
    return Fake


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    classes = {}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    for jid in (svc.SEARCH_JOB_ID, *[svc.SERIES_JOB_PREFIX + n for n in ("alpha", "beta")]):
        background_jobs.clear_job(jid)
    yield classes
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith("sources_"):
            background_jobs.clear_job(jid)


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _ok_routes(name):
    return {f"{HOST}/{name}/search/{i}?q=abc&token={SECRET}": html("<html>ok</html>")
            for i in (1, 2)}


def test_partial_results_when_one_source_fails(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    fakes["beta"] = _make("beta", search_exc=SourceUnavailable("down", retry_after=42.0))
    assert svc.start_search("abc") == {"job_id": "sources_search"}
    _wait("sources_search")
    out = svc.get_job_result("sources_search")
    r = out["result"]
    assert out["status"] == "done" and r["per_source_counts"] == {"alpha": 2, "beta": 0}
    assert {e["source"] for m in r["results"] for e in m["entries"]} == {"alpha"}
    assert r["errors"]["beta"]["status"] == 503
    assert r["errors"]["beta"]["details"]["retry_after"] == 42.0


def test_sources_filter_and_validation(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    fakes["beta"] = _make("beta", search_exc=RuntimeError("never called"))
    svc.start_search("abc", sources=["alpha"])
    _wait("sources_search")
    assert svc.get_job_result("sources_search")["result"]["errors"] == {}
    with pytest.raises(NotFoundError):
        svc.start_search("abc", sources=["nope"])
    with pytest.raises(InvalidInputError):
        svc.start_search("https://fake.invalid/x")
    with pytest.raises(InvalidInputError):
        svc.start_series("alpha", "http://evil/x")
    registry.set_enabled("beta", False)
    with pytest.raises(UnsupportedOperationError):
        svc.start_search("abc", sources=["beta"])


@pytest.mark.parametrize("bad", ["//169.254.169.254/x", "/abs/path", "\\\\evil\\x",
                                 "a\\b", "user@evil.invalid", "http:evil", "a/../../x",
                                 "a b", "a\nb", "a\x00b"])
def test_series_id_that_could_change_the_host_is_refused(fakes, bad):
    fakes["alpha"] = _make("alpha")
    with pytest.raises(InvalidInputError):
        svc.start_series("alpha", bad)
    assert background_jobs.get_status(svc.SERIES_JOB_PREFIX + "alpha") is None


def test_series_id_with_a_relative_path_is_allowed(fakes):
    fakes["alpha"] = _make("alpha")
    assert svc.start_series("alpha", "KeHuan/20_b/bkceK.html") == {
        "job_id": svc.SERIES_JOB_PREFIX + "alpha"}
    _wait(svc.SERIES_JOB_PREFIX + "alpha")


def test_cancel_mid_search(fakes):
    def cancel_on_first(url):
        background_jobs.request_cancel("sources_search")

    fakes["alpha"] = _make("alpha", _ok_routes("alpha"), on_request=cancel_on_first)
    svc.start_search("abc")
    _wait("sources_search")
    r = svc.get_job_result("sources_search")["result"]
    assert r["cancelled"] is True
    assert r["results"] == [] and r["errors"] == {}


def test_second_start_conflicts_and_finished_is_cleared(fakes, monkeypatch):
    import threading
    gate = threading.Event()
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"), on_request=lambda u: gate.wait(5))
    svc.start_search("abc")
    with pytest.raises(ConflictError):
        svc.start_search("abc")
    gate.set()
    _wait("sources_search")
    assert svc.start_search("abc") == {"job_id": "sources_search"}
    _wait("sources_search")


def test_result_404_after_clear_and_for_foreign_ids(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    svc.start_search("abc")
    _wait("sources_search")
    background_jobs.clear_job("sources_search")
    with pytest.raises(NotFoundError):
        svc.get_job_result("sources_search")
    with pytest.raises(NotFoundError):
        svc.get_job_result("translate_1")


@pytest.mark.parametrize("exc,cls,status,check", [
    (SourceUnavailable("down", retry_after=30.0), DependencyUnavailableError, 503,
     lambda d: d["retry_after"] == 30.0),
    (ChallengeDetected("challenge", f"{HOST}/p?cf_token={SECRET}",
                       FailureReason.CLOUDFLARE_CHALLENGE), ConflictError, 409,
     lambda d: d["handoff"] is True and d["open_url"] == f"{HOST}/p"
     and d["reason"] == FailureReason.CLOUDFLARE_CHALLENGE.value),
    (NotSupportedError("no series"), UnsupportedOperationError, 400,
     lambda d: d["reason"] == "NOT_SUPPORTED"),
    (TermsProhibited("terms"), UnsupportedOperationError, 400,
     lambda d: d["reason"] == "TOS_PROHIBITED"),
    (ContentHidden("hidden"), UnsupportedOperationError, 400,
     lambda d: d["hint"] == "adult_toggle" and d["source"] == "alpha"),
    (ResponseTooLarge(), InvalidInputError, 422, lambda d: d["reason"] == "RESPONSE_REFUSED"),
    (UnsupportedEncoding(), InvalidInputError, 422, lambda d: d["reason"] == "RESPONSE_REFUSED"),
    (ResponseTooSlow(), DependencyUnavailableError, 503,
     lambda d: d["reason"] == "RESPONSE_REFUSED"),
])
def test_series_exception_mapping(fakes, exc, cls, status, check):
    fakes["alpha"] = _make("alpha", series_exc=exc)
    svc.start_series("alpha", "s1")
    st = _wait("sources_series_alpha")
    assert st["status"] == "error" and SECRET not in (st["error"] or "")
    with pytest.raises(cls) as ei:
        svc.get_job_result("sources_series_alpha")
    assert check(ei.value.details)
    assert status == svc._error_view(exc, "alpha")["status"]
    assert SECRET not in json.dumps(ei.value.details) + ei.value.message


def test_search_exception_mapping_per_source(fakes):
    fakes["alpha"] = _make("alpha", search_exc=TermsProhibited("terms"))
    fakes["beta"] = _make("beta", search_exc=ChallengeDetected(
        "c", f"{HOST}/x?a={SECRET}", FailureReason.BOT_CHALLENGE))
    svc.start_search("abc")
    _wait("sources_search")
    errs = svc.get_job_result("sources_search")["result"]["errors"]
    assert errs["alpha"]["status"] == 400 and errs["alpha"]["details"]["reason"] == "TOS_PROHIBITED"
    assert errs["beta"]["status"] == 409 and errs["beta"]["details"]["open_url"] == f"{HOST}/x"


def test_series_result_sorted_and_redacted(fakes):
    fakes["alpha"] = _make("alpha")
    svc.start_series("alpha", "s1")
    _wait("sources_series_alpha")
    out = svc.get_job_result("sources_series_alpha")
    r = out["result"]
    assert [c["chapter_id"] for c in r["chapters"]] == ["c1", "c2", "c10"]
    assert r["info"]["url"] == f"{HOST}/series/s1"
    dumped = json.dumps(out, ensure_ascii=False)
    assert SECRET not in dumped and "?" not in dumped and "#frag" not in dumped
    assert "C:\\" not in dumped and "Users" not in dumped
    assert svc.known_chapter_ids(out) == ["c1", "c2", "c10"]
    assert svc.known_chapter_ids(r) == ["c1", "c2", "c10"]


def test_search_result_has_no_query_strings_or_secrets(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    svc.start_search("abc")
    _wait("sources_search")
    dumped = json.dumps(svc.get_job_result("sources_search"), ensure_ascii=False)
    assert SECRET not in dumped and "?" not in dumped and "#frag" not in dumped
    assert f"{HOST}/alpha/series/1" in dumped


def test_known_chapter_ids_rejects_non_series(fakes):
    with pytest.raises(InvalidInputError):
        svc.known_chapter_ids({"kind": "search", "results": []})
    with pytest.raises(InvalidInputError):
        svc.known_chapter_ids({"kind": "series", "error": {"status": 503}})
    assert svc.known_chapter_ids({"kind": "series", "chapters": [
        {"chapter_id": "a"}, {"chapter_id": "a"}, {"chapter_id": ""}, {"chapter_id": "b"}]}) == ["a", "b"]


def test_running_series_job_names_its_series(fakes):
    """The series job id is per source, so a poll while it runs must say
    which series it is for (ids only; no line text in the payload)."""
    import threading
    gate = threading.Event()
    fakes["alpha"] = cls = _make("alpha")
    orig = cls.get_series

    def slow(self, series_id):
        gate.wait(5)
        return orig(self, series_id)

    cls.get_series = slow
    svc.start_series("alpha", "A")
    try:
        r = svc.get_job_result("sources_series_alpha")
        assert r["status"] in ("running", "queued")
        assert (r["source"], r["series_id"]) == ("alpha", "A")
        assert r["result"] is None
    finally:
        gate.set()
    _wait("sources_series_alpha")
    done = svc.get_job_result("sources_series_alpha")
    assert (done["source"], done["series_id"]) == ("alpha", "A")


def test_search_job_has_no_series_identity(fakes):
    fakes["alpha"] = _make("alpha", _ok_routes("alpha"))
    svc.start_search("abc")
    _wait("sources_search")
    r = svc.get_job_result("sources_search")
    assert "source" not in r and "series_id" not in r


def test_refused_response_with_an_unmapped_status_is_a_500_not_a_keyerror():
    exc = ResponseRefused("Refused.")
    exc.status = 418
    view = svc._error_view(exc, "alpha")
    assert view["status"] == 500 and view["code"] == svc.ServiceError.code
