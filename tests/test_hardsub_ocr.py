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

import hardsub_ocr


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


class TestExtractHardsubSubtitlesOrchestration:
    """ffmpeg frame extraction and the actual OCR call are mocked at their
    boundary -- neither ffmpeg nor tesseract is available in this
    environment, and this is testing the pipeline's own wiring (does it
    call things in the right order, with the right args, and shape its
    output correctly), not the frame-extraction or OCR engines themselves."""

    def test_wires_frames_through_band_detection_ocr_and_dedupe(self, monkeypatch, tmp_path):
        fake_frames = [(0.0, "f0.png"), (1.0, "f1.png"), (2.0, "f2.png")]
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec: fake_frames)
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths, **k: (0.8, 1.0))

        ocr_calls = []
        def fake_ocr(path, region, lang, backend, tesseract_cmd=None):
            ocr_calls.append((path, region, lang, backend))
            return {"f0.png": "hi", "f1.png": "hi", "f2.png": "bye"}[path]
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region", fake_ocr)

        progress_seen = []
        result = hardsub_ocr.extract_hardsub_subtitles(
            "/fake/video.mp4", language="zh", sample_interval=1.0,
            progress_cb=progress_seen.append, tmp_dir=str(tmp_path))

        assert result == [
            {"start": 0.0, "end": 2.0, "text": "hi"},
            {"start": 2.0, "end": 3.0, "text": "bye"},
        ]
        assert [c[1] for c in ocr_calls] == [(0.8, 1.0)] * 3
        assert progress_seen == [pytest.approx(1 / 3), pytest.approx(2 / 3), 1.0]

    def test_falls_back_to_bottom_quarter_when_band_undetected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec: [(0.0, "f0.png")])
        monkeypatch.setattr(hardsub_ocr, "detect_caption_band", lambda paths, **k: None)

        seen_regions = []
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                             lambda path, region, lang, backend, tesseract_cmd=None:
                             seen_regions.append(region) or "x")

        hardsub_ocr.extract_hardsub_subtitles("/fake/video.mp4", tmp_dir=str(tmp_path))
        assert seen_regions == [(0.75, 1.0)]

    def test_no_frames_returns_empty_without_calling_ocr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hardsub_ocr, "extract_frames",
                             lambda video_path, out_dir, interval_sec: [])
        called = []
        monkeypatch.setattr(hardsub_ocr, "_ocr_frame_region",
                             lambda *a, **k: called.append(1))
        result = hardsub_ocr.extract_hardsub_subtitles("/fake/video.mp4", tmp_dir=str(tmp_path))
        assert result == []
        assert called == []
