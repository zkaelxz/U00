"""Vision engines as arms of the Benchmark Lab's OCR stage: config checks, the
worst-case estimate, cap stop, per-case failures and usage rows. Providers are
faked; nothing leaves the test."""
import io
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

import background_jobs
import db
import translate_engines
from engine_backends import vision
from services import benchmark_lab_service as svc
from services import settings_service, translate_service
from services.service_errors import InvalidInputError, MissingKeyError
from tests.test_benchmark_lab import _wait

REFERENCE = "你好世界"


def _png(size=(40, 20)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, "PNG")
    return out.getvalue()


def _case(label="frame", name="frame.png", content=None, reference=REFERENCE):
    os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
    with open(os.path.join(db.BENCHMARK_DIR, name), "wb") as fh:
        fh.write(content if content is not None else _png())
    return db.create_benchmark_case(label, "ocr", "hardsub", "zh", input_filename=name,
                                    reference_text=reference)


class _Calls(list):
    state = None


@pytest.fixture
def reads(isolated_db, monkeypatch):
    """Replaces the provider call; records each picture it was given."""
    calls = _Calls()
    state = {"text": REFERENCE, "fail": None}

    def fake(engine, image, mime, language="", on_done=None):
        calls.append(image)
        if state["fail"]:
            if on_done:
                on_done({})
            raise RuntimeError(state["fail"])
        usage = state.get("usage") or {"input_tokens": 300, "output_tokens": 5}
        if on_done:
            on_done(usage)
        return state["text"], usage

    monkeypatch.setattr(translate_engines, "read_image_text", fake)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test-key-12345678")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 0.0)
    calls.state = state
    return calls


def _run(configs, **kw):
    started = svc.start_run("ocr", configs, **kw)
    _wait()
    return started


class TestConfig:
    def test_a_vision_engine_is_accepted_with_an_offered_model(self):
        assert svc.check_config("ocr", {"engine": "claude"}) == {"engine": "claude", "model": None}
        model = next(e for e in translate_service.list_engines() if e["name"] == "claude")["models"][0]
        assert svc.check_config("ocr", {"engine": "claude", "model": model})["model"] == model

    def test_an_unknown_model_is_refused(self):
        with pytest.raises(InvalidInputError):
            svc.check_config("ocr", {"engine": "claude", "model": "claude-made-up-9"})

    def test_an_ollama_tag_is_shape_checked(self):
        assert svc.check_config("ocr", {"engine": "ollama", "model": "gemma4:12b"})["model"] == "gemma4:12b"
        with pytest.raises(InvalidInputError):
            svc.check_config("ocr", {"engine": "ollama", "model": "../etc/passwd"})

    def test_deepseek_is_refused(self):
        with pytest.raises(InvalidInputError):
            svc.check_config("ocr", {"engine": "deepseek"})

    def test_local_engines_stay_as_they_were(self):
        assert svc.check_config("ocr", {"engine": "tesseract"}) == {"engine": "tesseract", "model": None}

    def test_the_model_identity_is_the_model_name(self):
        assert svc.model_identity("ocr", {"engine": "claude", "model": "m-1"}) == {"model": "m-1"}
        assert svc.model_identity("ocr", {"engine": "tesseract", "model": None}) == {"model": "tesseract"}


class TestEstimate:
    def test_a_paid_vision_arm_has_a_real_cost_and_a_local_arm_none(self, reads):
        _case()
        est = svc.estimate("ocr", [{"engine": "claude"}, {"engine": "tesseract"}])
        by_engine = {c["engine"]: c for c in est["configs"]}
        assert by_engine["claude"]["estimated_cost_usd"] > 0 and by_engine["claude"]["cap_applies"]
        assert by_engine["tesseract"]["estimated_cost_usd"] == 0 and not by_engine["tesseract"]["cap_applies"]
        assert est["estimated_cost_usd"] == by_engine["claude"]["estimated_cost_usd"]

    def test_a_local_vision_model_is_free(self, reads):
        _case()
        (cfg,) = svc.estimate("ocr", [{"engine": "ollama", "model": "gemma4:12b"}])["configs"]
        assert cfg["estimated_cost_usd"] == 0 and not cfg["cap_applies"]

    def test_a_missing_key_refuses_the_run(self, reads, monkeypatch):
        _case()
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
        with pytest.raises(MissingKeyError):
            svc.start_run("ocr", [{"engine": "claude"}])


class TestRun:
    def test_a_read_is_scored_by_cer_and_logged_once(self, reads):
        _case()
        reads.state["text"] = "你好世"
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        run = svc.get_run(sid)
        (res,) = run["results"]
        assert res["metric"] == "cer" and res["score"] == pytest.approx(0.75)
        assert res["cost_usd"] > 0 and run["run"]["total_cost_usd"] == pytest.approx(res["cost_usd"])
        assert run["run"]["model"]
        assert db.get_month_spend() == pytest.approx(res["cost_usd"])
        assert len(reads) == 1

    def test_a_read_that_would_pass_the_cap_is_not_sent(self, reads):
        _case()
        worst = svc.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"]
        (sid,) = _run([{"engine": "claude"}], max_cost_usd=worst / 2)["session_ids"]
        run = svc.get_run(sid)
        assert run["run"]["status"] == "stopped_cap" and not reads and not run["results"]

    def test_the_run_stops_midway_once_real_spend_leaves_too_little(self, reads):
        for i in range(3):
            _case(f"f{i}", f"f{i}.png")
        worst = svc.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"] / 3
        reads.state["usage"] = {"input_tokens": 1_000_000, "output_tokens": 0}
        (sid,) = _run([{"engine": "claude"}], max_cost_usd=worst * 1.5)["session_ids"]
        run = svc.get_run(sid)
        assert run["run"]["status"] == "stopped_cap"
        assert len(run["results"]) == 1 and len(reads) == 1

    def test_a_failed_read_is_that_cases_error_and_redacted(self, reads):
        good = _case("good", "good.png")
        bad = _case("bad", "bad.png")
        reads.state["fail"] = "boom sk-ant-abcdefghijklmnopqrstuvwxyz123456"
        (sid,) = _run([{"engine": "claude"}], case_ids=[bad])["session_ids"]
        run = svc.get_run(sid)
        assert run["run"]["error_count"] == 1 and run["run"]["status"] == "done"
        assert "abcdefghijklmnop" not in run["results"][0]["error"]
        assert run["results"][0]["score"] == 0.0
        assert good  # the other case is untouched

    def test_a_picture_over_the_read_caps_is_that_cases_error_and_sends_nothing(self, reads):
        _case("huge", "huge.png", content=_png((6000, 6000)))
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        run = svc.get_run(sid)
        assert run["run"]["error_count"] == 1 and not reads
        assert "megapixel" in run["results"][0]["error"]
        assert run["run"]["total_cost_usd"] == 0

    def test_a_case_without_a_picture_is_its_error(self, reads):
        db.create_benchmark_case("nofile", "ocr", "hardsub", "zh", reference_text=REFERENCE)
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert svc.get_run(sid)["results"][0]["error"] == "This case has no input file."

    def test_a_vision_arm_runs_beside_a_local_one(self, reads, monkeypatch):
        import benchmark
        _case()
        monkeypatch.setattr(benchmark, "run_ocr_case", lambda case, backend="tesseract": {
            "output_text": REFERENCE, "duration_seconds": 0.1, "error": None, "score": None})
        sids = _run([{"engine": "tesseract"}, {"engine": "claude"}])["session_ids"]
        assert [svc.get_run(s)["run"]["status"] for s in sids] == ["done", "done"]
        assert len(reads) == 1

    def test_the_file_path_never_reaches_the_response(self, reads):
        _case()
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert db.BENCHMARK_DIR not in str(svc.get_run(sid))


class TestBounds:
    def test_a_cancel_during_a_read_ends_the_run_without_waiting_or_retrying(self, reads, monkeypatch):
        import threading
        import time
        _case()
        calls = []
        release = threading.Event()

        def slow(engine, prompt, data, mime):
            calls.append(1)
            release.wait(8)
            return REFERENCE, {}

        monkeypatch.setattr(vision, "_dispatch", slow)
        monkeypatch.setattr(translate_engines, "read_image_text", vision.read_image_text)
        started = time.monotonic()
        (sid,) = svc.start_run("ocr", [{"engine": "claude"}])["session_ids"]
        time.sleep(0.5)
        background_jobs.request_cancel(svc.JOB_ID)
        try:
            _wait()
            assert time.monotonic() - started < 6 and len(calls) == 1
            assert svc.get_run(sid)["run"]["status"] == "cancelled"
        finally:
            release.set()

    def test_a_picture_over_the_size_limit_is_that_cases_error_and_never_fully_read(self, reads, monkeypatch):
        from services import page_import_limits
        _case()
        monkeypatch.setattr(page_import_limits, "MAX_IMAGE_BYTES", 10)
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        run = svc.get_run(sid)
        assert run["run"]["error_count"] == 1 and not reads
        assert "size limit" in run["results"][0]["error"]

    def test_a_stored_name_cannot_leave_the_case_folder(self, reads, tmp_path):
        outside = tmp_path / "secret.png"
        outside.write_bytes(_png())
        db.create_benchmark_case("sneaky", "ocr", "hardsub", "zh", input_filename=f"../{outside.name}",
                                 reference_text=REFERENCE)
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert svc.get_run(sid)["run"]["error_count"] == 1 and not reads
