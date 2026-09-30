"""
tests/test_hardsub_ocr.py -- hardsub_ocr.py, the burned-in-caption OCR
pipeline for video (as opposed to ocr.py's still-image page OCR).

No real video or OCR engine is available to test against here (no ffmpeg
binary, no tesseract binary in this environment) -- detect_caption_band and
dedupe_into_cues are pure image/text logic and are tested directly against
synthetic frames built with OpenCV, which is what actually validates the
auto-detection heuristic rather than just exercising the code path. OCR
itself and ffmpeg's frame extraction are mocked at their boundary in the
orchestration tests, the same way run_transcribe_job's tests mock
transcribe_for_timing.
"""
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("cv2")  # hardsub_ocr.py imports cv2 at module level;
                            # requirements-media.txt, not core -- skip
                            # cleanly without it rather than fail collection
import hardsub_ocr
import ocr as ocr_module


def _make_frame(height=300, width=500, caption_rows=None, seed=0, caption_seed=None):
    """A synthetic 'video frame': a noisy, ever-changing background (stand-in
    for real video content moving frame to frame) plus an optional fixed
    caption band -- bright bar with dark blocky 'letters' -- that's either
    identical every frame (a real static caption) or itself changes
    (caption_seed set, standing in for the caption text actually changing
    between lines, which should still register as a stable BAND even
    though its content isn't byte-identical).

    The background is Gaussian-blurred after generating it per-pixel:
    real video content has spatial coherence (edges, smooth gradients,
    coherent moving objects), not per-pixel independent noise. Skipping
    the blur makes Canny see edges on nearly every pixel, which swamps
    the real signal and isn't a fair stand-in for actual footage.
    """
    import cv2
    rng = np.random.RandomState(seed)
    frame = rng.randint(0, 256, size=(height, width), dtype=np.uint8)
    frame = cv2.GaussianBlur(frame, (15, 15), 0)
    if caption_rows is not None:
        y0, y1 = caption_rows
        frame[y0:y1, :] = 230  # bright caption background bar
        crng = np.random.RandomState(caption_seed if caption_seed is not None else 12345)
        # A handful of dark blocks standing in for glyphs. Real caption
        # text keeps a consistent vertical baseline even as the actual
        # words change (font size/line position don't jump around from
        # one line to the next) -- so only the horizontal position varies
        # with caption_seed, matching that, rather than glyphs relocating
        # to a random row every frame (which no real subtitle does).
        baseline = y0 + max(2, (y1 - y0) // 3)
        for _ in range(15):
            cx = crng.randint(10, width - 30)
            frame[baseline:baseline + 6, cx:cx + 18] = 20
    return frame


class TestDetectCaptionBand:
    def test_finds_a_static_caption_over_noisy_background(self, tmp_path):
        height, width = 300, 500
        caption_rows = (250, 280)  # bottom band, matches a common real layout
        paths = []
        for i in range(15):
            frame = _make_frame(height, width, caption_rows=caption_rows, seed=i)
            p = str(tmp_path / f"f{i:03d}.png")
            import cv2
            cv2.imwrite(p, frame)
            paths.append(p)

        band = hardsub_ocr.detect_caption_band(paths, sample_count=12, band_frac=0.12)
        assert band is not None
        y_start, y_end = band[0] * height, band[1] * height
        # The detected band should meaningfully overlap the true caption band,
        # not land somewhere in the random, ever-changing background.
        overlap = max(0, min(y_end, caption_rows[1]) - max(y_start, caption_rows[0]))
        assert overlap >= (caption_rows[1] - caption_rows[0]) * 0.5

    def test_finds_a_caption_band_even_when_its_own_text_changes(self, tmp_path):
        """The band itself (bright bar) is what should be detected as
        stable -- not the exact glyph pixels, since real captions change
        text from line to line while staying in the same screen position."""
        height, width = 300, 500
        caption_rows = (250, 280)
        paths = []
        import cv2
        for i in range(15):
            frame = _make_frame(height, width, caption_rows=caption_rows,
                                 seed=i, caption_seed=i)
            p = str(tmp_path / f"f{i:03d}.png")
            cv2.imwrite(p, frame)
            paths.append(p)

        band = hardsub_ocr.detect_caption_band(paths)
        assert band is not None
        y_start, y_end = band[0] * height, band[1] * height
        overlap = max(0, min(y_end, caption_rows[1]) - max(y_start, caption_rows[0]))
        assert overlap >= (caption_rows[1] - caption_rows[0]) * 0.5

    def test_no_caption_still_returns_a_band_without_crashing(self, tmp_path):
        import cv2
        paths = []
        for i in range(6):
            frame = _make_frame(300, 500, caption_rows=None, seed=i)
            p = str(tmp_path / f"f{i:03d}.png")
            cv2.imwrite(p, frame)
            paths.append(p)
        band = hardsub_ocr.detect_caption_band(paths)
        assert band is not None
        assert 0.0 <= band[0] < band[1] <= 1.0

    def test_returns_none_for_fewer_than_two_frames(self):
        assert hardsub_ocr.detect_caption_band([]) is None
        assert hardsub_ocr.detect_caption_band(["/only/one.png"]) is None

    def test_pad_frac_widens_the_returned_band_beyond_the_raw_scoring_window(self, tmp_path):
        """Regression coverage for a real reported failure: a two-line
        caption taller than the band_frac scoring window got its second
        line clipped out of every OCR crop, because the raw window only
        has to overlap the true caption to score highest, not span all of
        it. pad_frac expands the returned band afterward so a line just
        outside the raw window still ends up inside the crop."""
        height, width = 300, 500
        caption_rows = (250, 280)  # 30px tall, band_frac=0.12 * 300 = 36px raw window
        paths = []
        import cv2
        for i in range(15):
            frame = _make_frame(height, width, caption_rows=caption_rows, seed=i)
            p = str(tmp_path / f"f{i:03d}.png")
            cv2.imwrite(p, frame)
            paths.append(p)

        unpadded = hardsub_ocr.detect_caption_band(paths, band_frac=0.12, pad_frac=0.0)
        padded = hardsub_ocr.detect_caption_band(paths, band_frac=0.12, pad_frac=0.6)

        raw_rows = (unpadded[1] - unpadded[0]) * height
        padded_rows = (padded[1] - padded[0]) * height
        assert padded_rows > raw_rows
        assert padded[0] <= unpadded[0]
        assert padded[1] >= unpadded[1]

    def test_pad_frac_clamps_to_frame_bounds(self, tmp_path):
        height, width = 300, 500
        caption_rows = (0, 20)  # right at the top edge -- padding above would go negative
        paths = []
        import cv2
        for i in range(15):
            frame = _make_frame(height, width, caption_rows=caption_rows, seed=i)
            p = str(tmp_path / f"f{i:03d}.png")
            cv2.imwrite(p, frame)
            paths.append(p)
        band = hardsub_ocr.detect_caption_band(paths, band_frac=0.12, pad_frac=0.6)
        assert band[0] >= 0.0
        assert band[1] <= 1.0


class TestUpscaleForOcr:
    def test_a_short_crop_is_upscaled_to_the_minimum_height(self):
        crop = np.zeros((40, 200, 3), dtype=np.uint8)
        result = hardsub_ocr._upscale_for_ocr(crop, min_height=120)
        assert result.shape[0] >= 120

    def test_a_tall_crop_is_returned_unchanged(self):
        crop = np.zeros((200, 500, 3), dtype=np.uint8)
        result = hardsub_ocr._upscale_for_ocr(crop, min_height=120)
        assert result.shape == crop.shape

    def test_scale_is_capped_at_max_scale(self):
        crop = np.zeros((10, 50, 3), dtype=np.uint8)
        result = hardsub_ocr._upscale_for_ocr(crop, min_height=120, max_scale=3.0)
        assert result.shape[0] == 30  # 10 * 3.0, not 10 * 12 to reach 120

    def test_zero_height_crop_is_returned_unchanged_without_crashing(self):
        crop = np.zeros((0, 50, 3), dtype=np.uint8)
        result = hardsub_ocr._upscale_for_ocr(crop)
        assert result.shape[0] == 0


class TestOcrFrameRegionUpscales:
    """Proves _ocr_frame_region actually applies the upscale to what gets
    OCR'd, end to end through a real cropped/written PNG -- not just that
    the helper function works in isolation."""

    def test_a_short_caption_crop_is_upscaled_before_ocr(self, monkeypatch, tmp_path):
        import cv2
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        frame_path = str(tmp_path / "frame.png")
        cv2.imwrite(frame_path, frame)

        seen_shapes = []
        def fake_tesseract(image_path, lang="chi_sim", tesseract_cmd=None):
            seen_shapes.append(cv2.imread(image_path).shape)
            return "text"
        monkeypatch.setattr(ocr_module, "extract_text_tesseract", fake_tesseract)

        # region (0.9, 1.0) of a 300px-tall frame is a 30px-tall crop --
        # well under the 120px floor, so it must come out upscaled (capped
        # at the default max_scale of 3x, so 90px here, not the full 120).
        hardsub_ocr._ocr_frame_region(frame_path, (0.9, 1.0), "chi_sim", "tesseract")

        assert seen_shapes[0][0] == 90


class TestDedupeIntoCues:
    def test_collapses_repeated_frames_into_one_cue(self):
        timed = [(0.0, "hello"), (1.0, "hello"), (2.0, "hello"), (3.0, "world")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0)
        assert len(cues) == 2
        assert cues[0] == {"start": 0.0, "end": 3.0, "text": "hello"}
        assert cues[1] == {"start": 3.0, "end": 4.0, "text": "world"}

    def test_blank_frames_end_a_cue_without_starting_one(self):
        timed = [(0.0, "hi"), (1.0, ""), (2.0, "  "), (3.0, "bye")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0)
        assert len(cues) == 2
        assert cues[0]["text"] == "hi"
        assert cues[1]["text"] == "bye"

    def test_whitespace_only_differences_still_merge(self):
        timed = [(0.0, "hi there"), (1.0, "hi  there"), (2.0, " hithere ")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0)
        assert len(cues) == 1
        assert cues[0]["start"] == 0.0
        assert cues[0]["end"] == 3.0

    def test_empty_input_returns_empty(self):
        assert hardsub_ocr.dedupe_into_cues([], interval_sec=1.0) == []

    def test_all_blank_returns_no_cues(self):
        timed = [(0.0, ""), (1.0, "  "), (2.0, "")]
        assert hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0) == []


class TestDedupeIntoCuesStabilityFilter:
    """min_consecutive_samples: regression coverage for a real known
    limitation (see hardsub_ocr.py's module docstring) -- a single
    misread frame, or one transition/fade frame, splitting one real
    static caption into three cues instead of one."""

    def test_a_single_frame_blip_is_absorbed_not_split_out(self):
        timed = [(0.0, "hello"), (1.0, "hello"), (2.0, "hallo"), (3.0, "hello"),
                 (4.0, "hello")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0, min_consecutive=2)
        assert cues == [{"start": 0.0, "end": 5.0, "text": "hello"}]

    def test_a_real_change_still_registers_once_it_repeats(self):
        timed = [(0.0, "hello"), (1.0, "hello"), (2.0, "world"), (3.0, "world"),
                 (4.0, "world")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0, min_consecutive=2)
        # "world" starts at its FIRST appearance (t=2), not the confirmation
        # frame (t=3) -- the filter shouldn't add latency to a real caption.
        assert cues == [{"start": 0.0, "end": 2.0, "text": "hello"},
                        {"start": 2.0, "end": 5.0, "text": "world"}]

    def test_a_caption_sampled_only_once_is_dropped_as_indistinguishable_from_noise(self):
        timed = [(0.0, "hello"), (1.0, "hello"), (2.0, "brief"), (3.0, "hello"),
                 (4.0, "hello")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0, min_consecutive=2)
        assert cues == [{"start": 0.0, "end": 5.0, "text": "hello"}]

    def test_a_blank_frame_still_ends_a_cue_immediately_regardless_of_the_filter(self):
        timed = [(0.0, "hi"), (1.0, "hi"), (2.0, ""), (3.0, "bye"), (4.0, "bye")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0, min_consecutive=2)
        assert cues == [{"start": 0.0, "end": 2.0, "text": "hi"},
                        {"start": 3.0, "end": 5.0, "text": "bye"}]

    def test_default_min_consecutive_of_one_matches_the_old_behavior(self):
        timed = [(0.0, "hello"), (1.0, "hello"), (2.0, "world")]
        cues = hardsub_ocr.dedupe_into_cues(timed, interval_sec=1.0)
        assert cues == [{"start": 0.0, "end": 2.0, "text": "hello"},
                        {"start": 2.0, "end": 3.0, "text": "world"}]


class TestExtractHardsubSubtitlesOrchestration:
    """ffmpeg frame extraction and the actual OCR call are mocked at their
    boundary -- neither ffmpeg nor tesseract is available in this
    environment, and this is testing the pipeline's own wiring (does it
    call things in the right order, with the right args, and shape its
    output correctly), not the frame-extraction or OCR engines themselves."""

    def test_wires_frames_through_band_detection_ocr_and_dedupe(self, monkeypatch, tmp_path):
        fake_frames = [(0.0, "f0.png"), (1.0, "f1.png"), (2.0, "f2.png")]
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec, job_id=None: fake_frames)
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths, **k: (0.8, 1.0))

        ocr_calls = []
        def fake_ocr(path, region, lang, backend, tesseract_cmd=None):
            ocr_calls.append((path, region, lang, backend))
            return {"f0.png": "hi", "f1.png": "hi", "f2.png": "bye"}[path]
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region", fake_ocr)

        progress_seen = []
        result = hardsub_ocr.extract_hardsub_subtitles(
            "/fake/video.mp4", language="zh", sample_interval=1.0,
            progress_cb=progress_seen.append, tmp_dir=str(tmp_path),
            min_consecutive_samples=1)  # testing wiring/order, not the stability filter

        assert result == [
            {"start": 0.0, "end": 2.0, "text": "hi"},
            {"start": 2.0, "end": 3.0, "text": "bye"},
        ]
        assert [c[1] for c in ocr_calls] == [(0.8, 1.0)] * 3
        assert progress_seen == [pytest.approx(1 / 3), pytest.approx(2 / 3), 1.0]

    def test_falls_back_to_bottom_quarter_when_band_undetected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec, job_id=None: [(0.0, "f0.png")])
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths, **k: None)

        seen_regions = []
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                             lambda path, region, lang, backend, tesseract_cmd=None:
                             seen_regions.append(region) or "x")

        hardsub_ocr.extract_hardsub_subtitles("/fake/video.mp4", tmp_dir=str(tmp_path))
        assert seen_regions == [(0.75, 1.0)]

    def test_no_frames_returns_empty_without_calling_ocr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec, job_id=None: [])
        called = []
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                             lambda *a, **k: called.append(1))
        result = hardsub_ocr.extract_hardsub_subtitles("/fake/video.mp4", tmp_dir=str(tmp_path))
        assert result == []
        assert called == []

    def test_defaults_to_requiring_two_consecutive_samples(self, monkeypatch, tmp_path):
        """A single-frame OCR blip on an otherwise-static caption is a real
        confirmed failure mode (see hardsub_ocr.py's module docstring) --
        the default pipeline call must actually use the stability filter,
        not just have it available as an unused option."""
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec, job_id=None:
                             [(0.0, "f0.png"), (1.0, "f1.png"), (2.0, "f2.png"), (3.0, "f3.png")])
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths, **k: (0.8, 1.0))
        texts = {"f0.png": "hello", "f1.png": "hello", "f2.png": "glitch", "f3.png": "hello"}
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                             lambda path, region, lang, backend, tesseract_cmd=None: texts[path])

        result = hardsub_ocr.extract_hardsub_subtitles(
            "/fake/video.mp4", sample_interval=1.0, tmp_dir=str(tmp_path))

        assert result == [{"start": 0.0, "end": 4.0, "text": "hello"}]


class TestHardsubCancel:
    def _frames(self, tmp_path, n=3):
        paths = []
        for i in range(n):
            p = tmp_path / f"f{i}.png"
            p.write_bytes(b"x")
            paths.append((float(i), str(p)))
        return paths

    def test_cancel_check_stops_the_frame_loop_and_removes_the_frame_dir(self, tmp_path, monkeypatch):
        import background_jobs
        seen = {}
        frames = self._frames(tmp_path)

        def fake_extract(video, out_dir, interval_sec=1.0, job_id=None):
            seen["dir"] = out_dir
            seen["job_id"] = job_id
            return frames
        ocr_calls = []
        monkeypatch.setattr(hardsub_ocr, "extract_frames", fake_extract)
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths: (0.8, 1.0))
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                            lambda *a, **k: ocr_calls.append(a) or "t")
        monkeypatch.setattr(ocr_module, "resolve_tesseract_lang", lambda *a, **k: "chi_sim")

        def cancel():
            if ocr_calls:
                raise background_jobs.JobCancelled("j")
        with pytest.raises(background_jobs.JobCancelled):
            hardsub_ocr.extract_hardsub_subtitles("v.mp4", job_id="j", cancel_check=cancel,
                                                  tmp_dir=str(tmp_path))
        assert len(ocr_calls) == 1
        assert seen["job_id"] == "j"
        assert not os.path.exists(seen["dir"])

    def test_extract_frames_routes_ffmpeg_through_run_cancellable_with_a_timeout(self, tmp_path, monkeypatch):
        import background_jobs
        got = {}
        monkeypatch.setattr(background_jobs, "run_cancellable",
                            lambda job_id, cmd, **k: got.update(job_id=job_id, cmd=cmd, **k))
        hardsub_ocr.extract_frames("v.mp4", str(tmp_path / "o"), 1.0, job_id="j")
        assert got["job_id"] == "j" and got["cmd"][0] == "ffmpeg"
        assert got["timeout"] == hardsub_ocr.EXTRACT_FRAMES_TIMEOUT_SECONDS

    def test_extract_frames_without_a_job_still_has_a_timeout(self, tmp_path, monkeypatch):
        got = {}
        monkeypatch.setattr(hardsub_ocr.subprocess, "run",
                            lambda cmd, **k: got.update(k))
        hardsub_ocr.extract_frames("v.mp4", str(tmp_path / "o"), 1.0)
        assert got["timeout"] == hardsub_ocr.EXTRACT_FRAMES_TIMEOUT_SECONDS
