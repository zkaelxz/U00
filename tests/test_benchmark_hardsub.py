"""
Hard-sub cases in the Benchmark Lab (services/benchmark_hardsub_service.py and
benchmark_hardsub_read_service.py): pictures of a line's subtitle band, the clarity filter, a read that
must repeat in two pictures, window scoring, and the read-then-translate arm. ffmpeg, the vision
provider and the translation engine are faked; nothing leaves the test.
"""
import io
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFilter

import background_jobs
import db
import translate_engines
from core import Line
from services import benchmark_hardsub_read_service as read_svc
from services import benchmark_hardsub_service as hs
from services import benchmark_lab_service as lab
from services import settings_service, translate_service
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError
from tests.test_benchmark_lab import _wait
from lib.errors import DependencyUnavailableError

TEXT = "你好吗"


def _picture(kind: str) -> bytes:
    """'sharp': light bars on black (text-like edges); 'blurred': the same, smeared; 'dark': near black."""
    im = Image.new("L", (300, 60), 8 if kind == "dark" else 0)
    if kind != "dark":
        draw = ImageDraw.Draw(im)
        for x in range(10, 280, 18):
            draw.rectangle([x, 15, x + 7, 45], fill=255)
        if kind == "blurred":
            im = im.filter(ImageFilter.GaussianBlur(9))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def ffmpeg(monkeypatch):
    """Fake ffmpeg: writes the next queued kind of picture to the output path and records the command."""
    state = {"kinds": [], "calls": [], "fail": False}

    def run(cmd, **kw):
        state["calls"].append((cmd, kw))
        if state["fail"]:
            raise hs.subprocess.CalledProcessError(1, cmd)
        kind = state["kinds"].pop(0) if state["kinds"] else "sharp"
        with open(cmd[-1], "wb") as fh:
            fh.write(_picture(kind))

    monkeypatch.setattr(hs.subprocess, "run", run)
    monkeypatch.setattr(hs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    return state


def _title(isolated_db, en=("Hello", "Are you well", "Goodbye"), video=True):
    did = db.create_drama(title_en="Moon Drama", source_language="zh",
                          source_video_filename="video.mp4" if video else None)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    if video:
        open(os.path.join(ddir, "video.mp4"), "wb").write(b"x")
    texts = [TEXT, "我很好", "再见"]
    db.save_lines(did, [Line(i, i * 4.0, i * 4.0 + 3.0, texts[i], en[i] if en else "") for i in range(3)])
    return did, [r["id"] for r in db.load_lines(did)]


def _frames(case_id):
    return read_svc.load(db.get_benchmark_case(case_id))["frames"]


class TestCaseCreation:
    def test_a_picture_can_be_grabbed_on_a_data_folder_that_never_held_a_case(self, isolated_db, ffmpeg):
        import shutil
        shutil.rmtree(db.BENCHMARK_DIR, ignore_errors=True)
        name = hs._grab("video.mp4", 1.0, (0.7, 1.0))
        assert os.path.exists(os.path.join(db.BENCHMARK_DIR, name))

    def test_three_pictures_of_the_band_become_one_ocr_case(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        out = hs.add_lines(None, did, [ids[0]])
        assert out == {"set_name": "hard-sub", "added": 1, "skipped": 0, "unclear": 0}
        (case,) = db.list_benchmark_cases("ocr")
        assert case["reference_text"] == TEXT and case["source_language"] == "zh"
        details = read_svc.load(case)
        assert len(details["frames"]) == 3 and details["translation"] == "Hello"
        assert details["translation_source"] == "line" and details["band"] == [0.7, 1.0]
        # Fixed fractions of the line's 3-second span, not the line's edges.
        assert [f["t"] for f in details["frames"]] == [0.6, 1.5, 2.4]
        assert case["input_filename"] in {f["file"] for f in details["frames"]}
        assert sorted(os.listdir(db.BENCHMARK_DIR)) == sorted(f["file"] for f in details["frames"])

    def test_the_stored_picture_is_the_sharpest_clear_one(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        hs.add_lines(None, did, [ids[0]])
        (case,) = db.list_benchmark_cases("ocr")
        frames = _frames(case["id"])
        best = max(frames, key=lambda f: f["sharp"])
        assert case["input_filename"] == best["file"]

    def test_dark_and_blurred_pictures_are_not_kept(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        ffmpeg["kinds"] = ["dark", "sharp", "blurred"]
        hs.add_lines(None, did, [ids[0]])
        (case,) = db.list_benchmark_cases("ocr")
        assert len(_frames(case["id"])) == 1
        assert len(os.listdir(db.BENCHMARK_DIR)) == 1

    def test_a_line_with_no_clear_picture_adds_nothing_and_leaves_no_file(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        ffmpeg["kinds"] = ["dark", "blurred", "dark"]
        with pytest.raises(InvalidInputError):
            hs.add_lines(None, did, [ids[0]])
        assert db.list_benchmark_cases("ocr") == [] and os.listdir(db.BENCHMARK_DIR) == []

    def test_one_unclear_line_is_counted_while_the_others_are_added(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        ffmpeg["kinds"] = ["dark", "dark", "dark"]
        out = hs.add_lines(None, did, ids[:2])
        assert (out["added"], out["unclear"]) == (1, 1)

    def test_a_failed_grab_leaves_no_file_and_names_no_path(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        ffmpeg["fail"] = True
        with pytest.raises(DependencyUnavailableError) as err:
            hs.add_lines(None, did, [ids[0]])
        assert db.BENCHMARK_DIR not in str(err.value) and "video.mp4" not in str(err.value)
        assert os.listdir(db.BENCHMARK_DIR) == []

    def test_every_grab_is_bounded_and_local_only(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        hs.add_lines(None, did, [ids[0]])
        for cmd, kw in ffmpeg["calls"]:
            assert kw["timeout"] == hs.FRAME_TIMEOUT_SECONDS
            assert "-protocol_whitelist" in cmd and "-frames:v" in cmd
            vf = cmd[cmd.index("-vf") + 1]
            assert "crop=iw:ih*0.3000:0:ih*0.7000" in vf and f"min({hs.MAX_FRAME_WIDTH},iw)" in vf

    def test_the_band_is_fractions_with_a_minimum_height(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        for top, bottom in ((0.9, 0.95), (-0.1, 0.5), (0.5, 1.5), (0.6, 0.6), ("70%", 1.0), (True, 1.0)):
            with pytest.raises(InvalidInputError):
                hs.add_lines(None, did, [ids[0]], band_top=top, band_bottom=bottom)
        hs.add_lines(None, did, [ids[0]], band_top=0.0, band_bottom=0.3)
        assert "ih*0.3000:0:ih*0.0000" in ffmpeg["calls"][0][0][ffmpeg["calls"][0][0].index("-vf") + 1]

    def test_without_an_english_column_there_is_no_reference_translation(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db, en=None)
        hs.add_lines(None, did, [ids[0]])
        (case,) = db.list_benchmark_cases("ocr")
        details = read_svc.load(case)
        assert details["translation"] is None and details["translation_source"] is None

    def test_adding_twice_skips_and_the_caps_hold(self, isolated_db, ffmpeg, monkeypatch):
        did, ids = _title(isolated_db)
        hs.add_lines(None, did, [ids[0]])
        assert hs.add_lines(None, did, [ids[0]])["skipped"] == 1 and len(db.list_benchmark_cases("ocr")) == 1
        with pytest.raises(InvalidInputError):
            hs.add_lines(None, did, list(range(1, hs.MAX_LINES_PER_REQUEST + 2)))
        monkeypatch.setattr(hs, "MAX_CASES_PER_TITLE", 1)
        with pytest.raises(InvalidInputError):
            hs.add_lines(None, did, [ids[1]])

    def test_a_missing_video_or_line_is_refused(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db, video=False)
        with pytest.raises(InvalidInputError):
            hs.add_lines(None, did, [ids[0]])
        did, ids = _title(isolated_db)
        with pytest.raises(NotFoundError):
            hs.add_lines(None, did, [10 ** 6])

    def test_deleting_the_case_removes_every_picture(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        hs.add_lines(None, did, [ids[0]])
        (case,) = db.list_benchmark_cases("ocr")
        lab.delete_case(case["id"])
        assert os.listdir(db.BENCHMARK_DIR) == []

    def test_the_api_shape_carries_no_picture_name(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        hs.add_lines(None, did, [ids[0]])
        listed = lab.list_cases("ocr")["cases"]
        assert listed[0]["hard_sub"] is True and listed[0]["has_input_file"] is True
        assert "frame_" not in str(listed) and "hardsub" not in listed[0]


class TestClarityMeasures:
    def test_sharp_text_passes_and_the_others_do_not(self, tmp_path):
        results = {}
        for kind in ("sharp", "blurred", "dark"):
            path = tmp_path / f"{kind}.png"
            path.write_bytes(_picture(kind))
            results[kind] = hs.is_clear(*hs.measure(str(path)))
        assert results == {"sharp": True, "blurred": False, "dark": False}


class TestRefusals:
    @pytest.mark.parametrize("text", [
        "I'm sorry, I can't read the text in this image.", "I cannot make out any text.",
        "There is no visible text in the image.", "Unable to read this image.", "...", "", "???"])
    def test_apologies_and_nothing_are_refusals(self, text):
        assert read_svc.looks_like_refusal(text)

    @pytest.mark.parametrize("text", ["你好吗", "Are you well?", "I can't wait to see you", "No, thank you"])
    def test_captions_are_not(self, text):
        assert not read_svc.looks_like_refusal(text)

    def test_two_reads_agree_despite_a_character_or_two(self):
        assert read_svc.agree("你好吗,我们走吧", "你好吗 我们走吧!")
        assert read_svc.agree("Are you well today?", "Are you well today")
        assert not read_svc.agree("Are you well", "Goodbye now")
        assert read_svc.confirmed([None, "abc def", "abc def"]) == "abc def"
        assert read_svc.confirmed(["abc", None, "xyz"]) is None


# ---- runs --------------------------------------------------------------------

class _Calls(list):
    state = None


class Translator:
    """Paid-looking engine: answers `EN <source>` and remembers what it was asked."""
    name = "claude"
    sources = []

    def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 10, "output_tokens": 10}

    def translate_batch(self, zh_lines, context):
        type(self).sources.extend(zh_lines)
        return [f"EN {z}" for z in zh_lines]


@pytest.fixture
def reads(isolated_db, ffmpeg, monkeypatch):
    """Replaces the provider call: replies come from a queue (the last one repeats)."""
    calls = _Calls()
    state = {"replies": [TEXT], "fail": None, "usage": {"input_tokens": 300, "output_tokens": 5}}

    def fake(engine, image, mime, language="", on_done=None):
        calls.append(image)
        if state["fail"]:
            if on_done:
                on_done({})
            raise RuntimeError(state["fail"])
        text = state["replies"].pop(0) if len(state["replies"]) > 1 else state["replies"][0]
        if on_done:
            on_done(state["usage"])
        return text, state["usage"]

    monkeypatch.setattr(translate_engines, "read_image_text", fake)
    monkeypatch.setitem(translate_engines.ENGINES, "claude", Translator)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test-key-12345678")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 0.0)
    Translator.sources = []
    background_jobs.clear_all_jobs()
    calls.state = state
    yield calls
    background_jobs.clear_all_jobs()


def _case(isolated_db, line=0, **kw):
    did, ids = _title(isolated_db, **kw)
    hs.add_lines(None, did, [ids[line]])
    return db.list_benchmark_cases("ocr")[-1]["id"]


def _run(configs, **kw):
    started = lab.start_run("ocr", configs, **kw)
    _wait()
    return started


class TestReadingRun:
    def test_two_agreeing_pictures_confirm_a_read_and_the_third_is_not_sent(self, reads):
        _case(reads)
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert len(reads) == 2 and res["output_text"] == TEXT and res["score"] == 1.0 and res["metric"] == "cer"
        assert res["recognised_text"] == TEXT and res["error"] is None

    def test_a_one_off_misread_is_outvoted_by_the_third_picture(self, reads):
        _case(reads)
        reads.state["replies"] = ["你好码", TEXT, TEXT]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert len(reads) == 3 and res["output_text"] == TEXT and res["score"] == 1.0

    def test_a_read_seen_in_only_one_picture_is_unreadable_and_left_out_of_the_scores(self, reads):
        _case(reads)
        reads.state["replies"] = ["甲乙丙", "丁戊己", "庚辛壬"]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        run = lab.get_run(sid)
        (res,) = run["results"]
        assert res["score"] is None and res["passed"] is None and res["error"].startswith("Unreadable")
        assert res["output_text"] == ""
        assert run["run"]["scored_count"] == 0 and run["run"]["error_count"] == 0
        assert run["run"]["aggregate_score"] is None
        assert "unreadable" in run["run"]["note"]
        assert res["cost_usd"] > 0

    def test_refusals_are_never_stored_and_do_not_count_as_a_read(self, reads):
        _case(reads)
        reads.state["replies"] = ["I'm sorry, I can't read this image.", "I'm sorry, I can't read this image.", TEXT]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["score"] is None and "sorry" not in str(res).lower()
        assert not res["output_text"] and not res["recognised_text"]

    def test_a_refusal_never_becomes_a_reference_through_agreement(self, reads):
        _case(reads)
        reads.state["replies"] = ["Unable to read this image."]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["score"] is None and res["recognised_text"] in (None, "")

    def test_a_caption_one_line_off_the_speech_is_scored_over_the_time_window(self, reads):
        _case(reads)
        reads.state["replies"] = ["我很好"]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["score"] == 1.0

    def test_the_whole_window_joined_matches_a_caption_spanning_lines(self, reads):
        _case(reads)
        reads.state["replies"] = [f"{TEXT} 我很好"]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert lab.get_run(sid)["results"][0]["score"] == 1.0

    def test_a_failed_read_is_that_cases_redacted_error(self, reads):
        _case(reads)
        reads.state["fail"] = "boom sk-test-key-12345678"
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["error"] and "sk-test-key" not in res["error"] and res["score"] == 0.0

    def test_usage_is_logged_once_per_picture_read(self, reads):
        _case(reads)
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert len(reads) == 2
        assert db.get_month_spend() == pytest.approx(lab.get_run(sid)["results"][0]["cost_usd"])

    def test_the_estimate_prices_every_picture_of_the_case(self, reads):
        _case(reads)
        single = lab.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"]
        (case,) = db.list_benchmark_cases("ocr")
        assert single > 0
        db.create_benchmark_case("plain", "ocr", "hardsub", "zh", input_filename=case["input_filename"],
                                 reference_text=TEXT)
        plain = next(c for c in db.list_benchmark_cases("ocr") if c["label"] == "plain")
        both = lab.estimate("ocr", [{"engine": "claude"}], case_ids=[plain["id"]])["estimated_cost_usd"]
        assert single == pytest.approx(3 * both)

    def test_the_cap_stops_before_a_picture_that_would_pass_it(self, reads):
        _case(reads)
        one = lab.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"] / 3
        (sid,) = _run([{"engine": "claude"}], max_cost_usd=one / 2)["session_ids"]
        assert lab.get_run(sid)["run"]["status"] == "stopped_cap" and not reads


class TestReadThenTranslate:
    ARM = {"engine": "claude", "translate": {"engine": "claude"}}

    def test_both_numbers_come_back_and_the_translation_is_scored_on_the_reference(self, reads):
        _case(reads)
        reads.state["replies"] = [TEXT]
        (sid,) = _run([self.ARM])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert Translator.sources == [TEXT]
        assert res["output_text"] == f"EN {TEXT}" and res["recognised_text"] == TEXT
        assert res["read_score"] == 1.0 and res["metric"] in ("chrf", "similarity")
        assert 0.0 <= res["score"] < 1.0   # "EN 你好吗" against "Hello"

    def test_a_misread_lowers_the_translation_but_not_below_a_read_score_of_its_own(self, reads):
        _case(reads)
        reads.state["replies"] = ["你好码"]
        (sid,) = _run([self.ARM])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["read_score"] < 1.0 and Translator.sources == ["你好码"]

    def test_the_estimate_adds_the_translation_and_the_cost_is_one_cap(self, reads):
        _case(reads)
        read_only = lab.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"]
        both = lab.estimate("ocr", [self.ARM])["estimated_cost_usd"]
        assert both > read_only

    def test_cancel_between_the_read_and_the_translation_sends_no_translation(self, reads, monkeypatch):
        _case(reads)
        real = read_svc.read_case

        def read_then_cancel(*a, **kw):
            out = real(*a, **kw)
            monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda job_id: True)
            return out

        monkeypatch.setattr(read_svc, "read_case", read_then_cancel)
        sid = lab.start_run("ocr", [self.ARM])["session_ids"][0]
        _wait()
        assert Translator.sources == [] and lab.get_run(sid)["results"] == []
        assert lab.get_run(sid)["run"]["status"] == "cancelled"

    def test_a_translation_that_would_pass_the_cap_is_not_sent(self, reads, monkeypatch):
        _case(reads)
        monkeypatch.setattr(lab, "_estimate_config", lambda cfg, cases: 10.0)
        reads_only = lab.estimate("ocr", [{"engine": "claude"}])["estimated_cost_usd"]
        (sid,) = _run([self.ARM], max_cost_usd=reads_only * 2)["session_ids"]
        assert Translator.sources == [] and lab.get_run(sid)["run"]["status"] == "stopped_cap"

    def test_a_case_without_a_reference_translation_refuses_the_run(self, reads):
        _case(reads, en=None)
        with pytest.raises(UnsupportedOperationError):
            lab.estimate("ocr", [self.ARM])

    def test_only_a_vision_arm_may_translate(self, reads):
        for stage, cfg in (("ocr", {"engine": "tesseract", "translate": {"engine": "claude"}}),
                           ("translation", {"engine": "claude", "translate": {"engine": "claude"}})):
            with pytest.raises(InvalidInputError):
                lab.check_config(stage, cfg)

    def test_the_translation_model_is_checked_like_any_other(self, reads):
        with pytest.raises(InvalidInputError):
            lab.check_config("ocr", {"engine": "claude", "translate": {"engine": "claude", "model": "made-up-9"}})

    def test_two_arms_that_differ_only_in_translation_are_a_real_comparison(self, reads):
        _case(reads)
        model = next(e for e in translate_service.list_engines() if e["name"] == "claude")["models"][0]
        est = lab.estimate("ocr", [self.ARM, {"engine": "claude", "translate": {"engine": "claude", "model": model}}])
        assert len(est["configs"]) == 2

    def test_a_missing_translation_key_refuses_the_run(self, reads, monkeypatch):
        _case(reads)
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, env_path=None: "sk-test-key-12345678" if name == "claude" else None)
        from services.service_errors import MissingKeyError
        arm = {"engine": "claude", "translate": {"engine": "deepseek"}}
        with pytest.raises(MissingKeyError):
            lab.start_run("ocr", [arm])


class TestTranslationLegIsCapped:
    """A free or local vision engine with a paid translation engine is still a paid arm: the caps govern it."""
    ARM = {"engine": "ollama", "model": "gemma4:12b", "translate": {"engine": "claude"}}

    def test_the_estimate_says_the_cap_applies_and_prices_the_translation(self, reads):
        _case(reads)
        (cfg,) = lab.estimate("ocr", [self.ARM])["configs"]
        assert cfg["cap_applies"] is True and cfg["estimated_cost_usd"] > 0

    def test_a_spent_monthly_cap_refuses_the_run_and_sends_nothing(self, reads, monkeypatch):
        _case(reads)
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 1.0)
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **k: 1.0)
        with pytest.raises(UnsupportedOperationError):
            lab.start_run("ocr", [self.ARM])
        assert not reads and Translator.sources == []

    def test_the_run_cap_stops_before_the_translation(self, reads):
        _case(reads)
        (sid,) = _run([self.ARM], max_cost_usd=1e-9)["session_ids"]
        assert Translator.sources == [] and lab.get_run(sid)["run"]["status"] == "stopped_cap"

    def test_a_free_translation_leg_on_a_free_vision_arm_is_uncapped(self, reads):
        _case(reads)
        arm = {"engine": "ollama", "model": "gemma4:12b", "translate": {"engine": "ollama", "model": "gemma4:12b"}}
        (cfg,) = lab.estimate("ocr", [arm])["configs"]
        assert cfg["cap_applies"] is False


class TestLabBugFixes:
    def test_an_edited_reference_is_what_the_main_line_is_scored_on(self, reads):
        from services import benchmark_clip_service as clip_svc
        case_id = _case(reads)
        clip_svc.edit_reference(case_id, "你们好")
        reads.state["replies"] = ["你们好"]
        (sid,) = _run([{"engine": "claude"}])["session_ids"]
        assert lab.get_run(sid)["results"][0]["score"] == 1.0

    def test_a_line_spoken_in_another_language_makes_a_case_in_that_language(self, isolated_db, ffmpeg):
        did, ids = _title(isolated_db)
        lines = db.load_lines(did)
        db.save_lines(did, [Line(r["idx"], r["start"], r["end"], r["zh"], r["en"], id=r["id"],
                                 lang="ko" if r["id"] == ids[0] else None) for r in lines])
        hs.add_lines(None, did, [ids[0], ids[1]])
        langs = {c["origin_line_id"]: c["source_language"] for c in db.list_benchmark_cases("ocr")}
        assert langs == {ids[0]: "ko", ids[1]: "zh"}

    def test_a_second_add_to_the_same_set_while_one_runs_is_refused(self, isolated_db, ffmpeg):
        from services.benchmark_reference_service import adding_to_set
        from services.service_errors import ConflictError
        did, ids = _title(isolated_db)
        with adding_to_set("hard-sub "):
            with pytest.raises(ConflictError):
                hs.add_lines(None, did, [ids[0]])
        assert db.list_benchmark_cases("ocr") == []
        assert hs.add_lines(None, did, [ids[0]])["added"] == 1

    def test_the_arena_counts_unreadable_cases_as_the_run_does(self, reads):
        _case(reads)
        reads.state["replies"] = ["甲乙丙", "丁戊己", "庚辛壬"]
        sids = _run([{"engine": "claude"}, {"engine": "claude", "model": "claude-sonnet-5-5"}])["session_ids"]
        out = lab.arena(sids)
        assert [r["error_count"] for r in out["runs"]] == [0, 0]
        assert [lab.get_run(s)["run"]["error_count"] for s in sids] == [0, 0]

    def test_a_failed_translation_step_keeps_the_reads_cost(self, reads, monkeypatch):
        from services import benchmark_vision_service as vision
        from services.service_errors import MissingKeyError
        _case(reads)
        (sid,) = lab.start_run("ocr", [TestReadThenTranslate.ARM])["session_ids"]
        _wait()
        paid = lab.get_run(sid)["results"][0]["cost_usd"]
        Translator.sources = []

        def no_key(cfg):
            raise MissingKeyError("claude")

        monkeypatch.setattr(vision, "_translator", no_key)
        (sid,) = _run([TestReadThenTranslate.ARM])["session_ids"]
        (res,) = lab.get_run(sid)["results"]
        assert res["error"] and Translator.sources == []
        assert res["cost_usd"] > 0 and res["cost_usd"] <= paid
