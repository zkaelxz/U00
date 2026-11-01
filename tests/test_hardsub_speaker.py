"""
Reading a title's hard subs speaker by speaker (services/hardsub_speaker_groups.py,
hardsub_speaker_read_service.py, hardsub_speaker_compare.py). ffmpeg and the vision provider are faked.
"""
import io
import json
import os
import threading
import time

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw

import background_jobs
import db
from engine_backends import engine_registry
from engine_backends import vision as engine_vision
from core import Line
from jobs import job_launch
from services import benchmark_hardsub_service as band_pictures
from services import hardsub_speaker_compare as compare
from services import hardsub_speaker_groups as groups
from services import hardsub_speaker_read_service as svc
from services import settings_service, translate_service
from lib.errors import InvalidInputError, NotFoundError, UnsupportedOperationError

WHITE, YELLOW, CYAN, RED = (255, 255, 255), (255, 230, 0), (0, 230, 230), (255, 40, 40)
# What the fake vision model "reads" from a crop, by the colour of its text.
SPOKEN = {"white": "你好吗", "yellow": "我很好", "cyan": "再见了"}


def _band(parts, size=(1280, 300)) -> Image.Image:
    """parts: [(x, y, colour)]: a row of text-like bars at each."""
    im = Image.new("RGB", size, (20, 20, 20))
    draw = ImageDraw.Draw(im)
    for x, y, colour in parts:
        for i in range(8):
            draw.rectangle([x + i * 24, y, x + i * 24 + 14, y + 30], fill=colour)
    return im


FOUR = [(20, 40, WHITE), (20, 150, CYAN), (900, 40, YELLOW), (900, 150, WHITE)]


class TestSplitBand:
    def test_four_speakers_by_colour_and_corner(self):
        found = groups.split_band(_band(FOUR))
        assert sorted(g.key for g in found) == ["cyan-left", "white-left", "white-right", "yellow-right"]

    def test_the_same_colour_in_two_corners_is_two_groups(self):
        found = groups.split_band(_band([(20, 40, WHITE), (900, 40, WHITE)]))
        assert sorted(g.key for g in found) == ["white-left", "white-right"]

    def test_a_band_without_text_has_no_groups(self):
        assert groups.split_band(_band([])) == []

    def test_a_speck_is_not_a_group(self):
        im = _band([(20, 40, WHITE)])
        ImageDraw.Draw(im).rectangle([1000, 100, 1003, 103], fill=YELLOW)
        assert [g.key for g in groups.split_band(im)] == ["white-left"]

    def test_a_group_box_covers_its_text(self):
        (g,) = groups.split_band(_band([(900, 40, YELLOW)]))
        assert g.box[0] <= 900 and g.box[2] >= 900 + 7 * 24 + 14 and g.box[1] <= 40 and g.box[3] >= 70


def _png(im) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def run(isolated_db, monkeypatch):
    state = {"band": FOUR, "replies": {}, "fail": False, "calls": [], "grabs": [],
             "usage": {"input_tokens": 300, "output_tokens": 5}}

    def grab(video, at, band):
        state["grabs"].append(at)
        name = f"frame_{len(state['grabs'])}.png"
        os.makedirs(db.BENCHMARK_DIR, exist_ok=True)
        with open(os.path.join(db.BENCHMARK_DIR, name), "wb") as fh:
            fh.write(_png(_band(state["band"])))
        return name

    def fake(engine, image, mime, language="", on_done=None):
        state["calls"].append(image)
        usage = state["usage"]
        if on_done:
            on_done(usage)
        if state["fail"]:
            raise RuntimeError("provider down sk-test-key-12345678")
        crop = Image.open(io.BytesIO(image)).convert("RGB")
        colour = max(crop.getcolors(10 ** 6), key=lambda c: (c[1] != (20, 20, 20), c[1] in (WHITE, YELLOW, CYAN), c[0]))[1]
        name = {WHITE: "white", YELLOW: "yellow", CYAN: "cyan"}.get(colour)
        return state["replies"].get(name, SPOKEN.get(name, "")), usage

    class Engine:
        name = "claude"

        def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
            self.model = model
            self.last_usage = {}

    monkeypatch.setattr(band_pictures, "_grab", grab)
    monkeypatch.setattr(band_pictures.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(engine_vision, "read_image_text", fake)
    monkeypatch.setitem(engine_registry.ENGINES, "claude", Engine)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "sk-test-key-12345678")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 0.0)
    background_jobs.clear_all_jobs()
    yield state
    background_jobs.clear_all_jobs()


def _title(speakers=True):
    did = db.create_drama(title_en="Four Voices", source_language="zh", source_video_filename="video.mp4")
    os.makedirs(db.drama_dir(did), exist_ok=True)
    open(os.path.join(db.drama_dir(did), "video.mp4"), "wb").write(b"x")
    db.save_lines(did, [Line(0, 0.0, 2.9, "你好吗", speaker="S1" if speakers else None),
                        Line(1, 3.0, 5.9, "我很好", speaker="S2" if speakers else None),
                        Line(2, 6.0, 8.9, "再见了", speaker="S1" if speakers else None)])
    return did


def _finish(did, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(svc.job_id(did))
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _read(did, **kw):
    svc.start(None, did, "claude", **kw)
    return _finish(did)["result"]


class TestReading:
    def test_each_speaker_group_gets_its_own_reference_lines(self, run):
        result = _read(_title())
        by_key = {g["key"]: g["lines"] for g in result["groups"]}
        assert set(by_key) == {"cyan-left", "white-left", "white-right", "yellow-right"}
        assert by_key["yellow-right"][0]["text"] == "我很好"
        # The same text in every stretch is one line spanning them, not one line per stretch.
        assert len(by_key["white-left"]) == 1 and by_key["white-left"][0]["start"] == 0.0
        assert by_key["white-left"][0]["end"] == 9.0
        assert result["stopped"] is None and result["unreadable"] == 0

    def test_two_agreeing_pictures_confirm_a_group_and_the_third_is_not_sent(self, run):
        run["band"] = [(900, 40, YELLOW)]
        _read(_title())
        assert len(run["calls"]) == 2 * 3   # three stretches, two reads each

    def test_a_group_with_no_text_is_unreadable_and_left_out_of_the_lines(self, run):
        run["replies"]["yellow"] = " "
        result = _read(_title())
        assert all(g["key"] != "yellow-right" or g["lines"] == [] for g in result["groups"])
        assert result["unreadable"] > 0

    def test_a_refusal_is_never_stored(self, run):
        run["replies"]["yellow"] = "I'm sorry, I can't read the text in this image."
        result = _read(_title())
        assert "sorry" not in str(result) and "yellow-right" in {g["key"] for g in result["groups"]}
        assert [g["lines"] for g in result["groups"] if g["key"] == "yellow-right"] == [[]]

    def test_unclear_pictures_are_skipped_and_counted(self, run, monkeypatch):
        run["band"] = []
        result = _read(_title())
        assert result["groups"] == [] and result["unclear_pictures"] > 0 and run["calls"] == []

    def test_the_result_names_no_path_or_key(self, run):
        run["fail"] = True
        result = _read(_title())
        assert result["stopped"] == "failed"
        assert "sk-test" not in str(result) and db.BENCHMARK_DIR not in str(result) and "video.mp4" not in str(result)

    def test_no_picture_is_left_behind(self, run):
        _read(_title())
        assert os.listdir(db.BENCHMARK_DIR) == []

    def test_usage_is_logged_once_per_read(self, run, monkeypatch):
        logged = []
        monkeypatch.setattr(svc.image_text_service.paid_calls, "record_call",
                            lambda *a, **k: logged.append(a), raising=True)
        _read(_title())
        assert len(logged) == len(run["calls"]) > 0

    def test_the_comparison_checks_the_transcript_against_the_groups(self, run):
        run["band"] = FOUR[:3]
        result = _read(_title())
        assert result["comparison"]["recall"] == 1.0
        # S1 speaks while white-left is shown, so the labels map onto groups.
        assert result["comparison"]["label_match"] is not None


class TestResultView:
    def test_a_finished_reading_returns_its_groups_and_comparison(self, run):
        did = _title()
        _read(did)
        got = svc.result(None, did)
        assert got["status"] == "done"
        assert {g["key"] for g in got["result"]["groups"]} == {"cyan-left", "white-left", "white-right", "yellow-right"}
        assert got["result"]["comparison"]["recall"] is not None

    def test_a_finished_reading_survives_an_app_restart(self, run):
        did = _title()
        first = _read(did)
        background_jobs.clear_all_jobs()
        got = svc.result(None, did)
        assert got["status"] == "done" and got["result"] == first

    def test_a_new_reading_in_progress_hides_the_saved_one(self, run):
        did = _title()
        _read(did)
        background_jobs.clear_all_jobs()
        gate = threading.Event()
        job_launch.start_job(svc.job_id(did), lambda jid: gate.wait(10), svc.job_id(did),
                             description="x", line_fields=())
        try:
            assert svc.result(None, did)["status"] == "running"
        finally:
            gate.set()

    def test_a_failed_new_reading_does_not_show_the_old_one_as_done(self, run):
        did = _title()
        _read(did)
        background_jobs.clear_all_jobs()

        def boom(jid):
            raise RuntimeError("ffmpeg exploded")
        job_launch.start_job(svc.job_id(did), boom, svc.job_id(did), description="x", line_fields=())
        _finish(did)
        got = svc.result(None, did)
        assert got["status"] != "done" and got["result"] is None

    def test_nothing_is_saved_for_a_title_deleted_during_its_reading(self, run):
        did = _title()
        folder = db.drama_dir(did)
        db.delete_drama(did)
        svc._save_result(did, {"groups": []})
        assert not os.path.exists(os.path.join(folder, svc.RESULT_FILE))

    def test_a_title_cannot_be_deleted_while_its_paid_reading_runs(self, run):
        from services import drama_service
        did = _title()
        gate = threading.Event()
        job_launch.start_job(svc.job_id(did), lambda jid: gate.wait(10), svc.job_id(did),
                             description="x", line_fields=())
        try:
            assert drama_service.job_running_for_drama(did)
        finally:
            gate.set()

    def test_a_title_never_read_is_not_found(self, run):
        with pytest.raises(NotFoundError):
            svc.result(None, _title())

    def test_a_reading_in_progress_has_no_result_yet(self, run):
        did = _title()
        gate = threading.Event()
        job_launch.start_job(svc.job_id(did), lambda jid: gate.wait(10), svc.job_id(did),
                             description="x", line_fields=())
        try:
            got = svc.result(None, did)
            assert got["status"] == "running" and got["result"] is None
        finally:
            gate.set()

    def test_a_corrupt_saved_file_counts_as_no_saved_reading(self, run):
        did = _title()
        for junk in ("{not json", ""):
            with open(svc._result_path(did), "w", encoding="utf-8") as fh:
                fh.write(junk)
            with pytest.raises(NotFoundError):
                svc.result(None, did)

    def test_a_stopped_reading_is_saved_and_comes_back_with_its_reason(self, run):
        did = _title()
        run["fail"] = True
        _read(did)
        background_jobs.clear_all_jobs()
        got = svc.result(None, did)
        assert got["status"] == "done" and got["result"]["stopped"] == "failed"
        assert "sk-test-key-12345678" not in json.dumps(got)

    def test_the_saved_reading_belongs_to_its_own_title(self, run):
        first, second = _title(), _title()
        _read(first)
        background_jobs.clear_all_jobs()
        assert svc.result(None, first)["status"] == "done"
        with pytest.raises(NotFoundError):
            svc.result(None, second)

    def test_a_member_cannot_read_another_members_private_title(self, run):
        did = _title()
        db.update_drama(did, is_private=1, owner_user_id=1)
        _read(did)
        background_jobs.clear_all_jobs()
        assert svc.result({"user_id": 1}, did)["status"] == "done"
        with pytest.raises(NotFoundError):
            svc.result({"user_id": 2}, did)


class TestEstimateAndCap:
    def test_the_estimate_is_the_worst_case_and_needs_no_key(self, run):
        did = _title()
        priced = svc.estimate(None, did, "claude", None, 4)
        assert priced["windows"] == 3 and priced["reads_at_most"] == 3 * 4 * 3 and priced["estimated_usd"] > 0
        assert svc.estimate(None, did, "claude", None, 2)["estimated_usd"] < priced["estimated_usd"]

    def test_the_run_stops_before_a_read_that_would_pass_its_cap(self, run):
        did = _title()
        one = svc.estimate(None, did, "claude", None, 1)["estimated_usd"] / 9
        refused = _read(did, max_cost_usd=one * 0.5)
        assert refused["stopped"] == "cap" and run["calls"] == [] and refused["cost_usd"] == 0
        background_jobs.clear_all_jobs()
        run["usage"] = {"input_tokens": 10 ** 6, "output_tokens": 5}
        result = _read(did, max_cost_usd=one * 1.5)
        assert result["stopped"] == "cap" and len(run["calls"]) == 1

    def test_a_paid_engine_with_a_zero_limit_is_refused(self, run):
        with pytest.raises(UnsupportedOperationError):
            svc.start(None, _title(), "claude", max_cost_usd=0)

    def test_an_engine_that_cannot_read_pictures_is_refused(self, run):
        with pytest.raises(UnsupportedOperationError):
            svc.estimate(None, _title(), "deepseek")

    def test_bad_inputs_are_refused(self, run):
        did = _title()
        for kw in ({"speaker_groups": 0}, {"speaker_groups": True}, {"band_top": 0.95, "band_bottom": 1.0}):
            with pytest.raises(InvalidInputError):
                svc.estimate(None, did, "claude", **kw)
        with pytest.raises(InvalidInputError):
            svc.start(None, did, "claude", max_cost_usd=-1)


class TestCompare:
    REF = [{"key": "white-left", "lines": [{"start": 0.0, "end": 3.0, "text": "你好吗"}]},
           {"key": "yellow-right", "lines": [{"start": 0.0, "end": 3.0, "text": "再见了"}]}]

    def test_a_missing_speaker_shows_in_that_groups_recall(self):
        heard = [{"start": 0.0, "end": 3.0, "zh": "你好吗"}]
        out = compare.compare(self.REF, heard)
        recall = {g["key"]: g["recall"] for g in out["groups"]}
        assert recall == {"white-left": 1.0, "yellow-right": 0.0}
        assert out["recall"] == 0.5 and out["precision"] == 1.0

    def test_speakers_in_either_order_score_the_same(self):
        assert compare.compare(self.REF, [{"start": 0.0, "end": 3.0, "zh": "再见了你好吗"}])["recall"] == 1.0

    def test_labels_map_to_groups_by_overlapping_time(self):
        ref = [{"key": "white-left", "lines": [{"start": 0.0, "end": 2.0, "text": "你好"}]},
               {"key": "yellow-right", "lines": [{"start": 2.0, "end": 4.0, "text": "很好"}]}]
        heard = [{"start": 0.0, "end": 2.0, "zh": "你好", "speaker": "A"}, {"start": 2.0, "end": 4.0, "zh": "很好", "speaker": "B"},
                 {"start": 3.0, "end": 4.0, "zh": "好", "speaker": "A"}]
        out = compare.compare(ref, heard)
        assert out["label_overlap"] == {"A": {"white-left": 2.0, "yellow-right": 1.0}, "B": {"yellow-right": 2.0}}
        assert out["label_match"] == round(4 / 5, 4)

    def test_a_line_across_two_spans_is_counted_once(self):
        ref = [{"key": "white-left", "lines": [{"start": 0.0, "end": 2.0, "text": "你好"}]},
               {"key": "yellow-right", "lines": [{"start": 2.0, "end": 4.0, "text": "很好"}]}]
        out = compare.compare(ref, [{"start": 0.0, "end": 4.0, "zh": "你好很好"}])
        assert out["recall"] == 1.0 and out["precision"] == 1.0

    def test_one_heard_character_is_not_claimed_by_two_speakers(self):
        ref = [{"key": "white-left", "lines": [{"start": 0.0, "end": 3.0, "text": "好"}]},
               {"key": "yellow-right", "lines": [{"start": 0.0, "end": 3.0, "text": "好"}]}]
        out = compare.compare(ref, [{"start": 0.0, "end": 3.0, "zh": "好"}])
        assert sum(g["recall"] for g in out["groups"]) == 1.0 and out["recall"] == 0.5

    def test_a_line_without_text_is_no_error(self):
        out = compare.compare(self.REF, [{"start": 0.0, "end": 3.0, "zh": None}])
        assert out["recall"] == 0.0 and out["precision"] is None

    def test_nothing_to_compare_is_none_not_zero(self):
        out = compare.compare([], [{"start": 0.0, "end": 1.0, "zh": "你好"}])
        assert out["recall"] is None and out["label_match"] is None
