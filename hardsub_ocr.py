"""
hardsub_ocr.py -- extracts subtitle text that's burned directly into a
video's pixels ("hardsubs"), for content where there's no separate audio
track worth transcribing that actually matches the caption -- an edited
compilation clip, a variety show, a short with a caption over background
music -- or where the on-screen caption is simply what should be
translated, whatever the audio happens to contain.

Distinct from ocr.py's page-image OCR (built for scanned novel/manga
pages -- one text block per still image): this operates on VIDEO. It has
to sample many frames, figure out WHERE on screen the caption sits
(auto-detected, not user-picked), and collapse many frames showing the
same caption into one timed subtitle cue instead of treating each sampled
frame as its own line.

Known limitations, honestly, not swept under the rug:
  - Auto-detection finds ONE band (the single highest-scoring region). A
    video with captions in two places at once (e.g. a stylized header at
    the top AND a live caption at the bottom) only gets the higher-scoring
    one by default.
  - Small per-frame OCR jitter on an otherwise-static caption (a
    misread character on one sampled frame) can split what's really one
    caption into two short adjacent cues, since consecutive frames are
    compared on normalized-but-exact text, not fuzzy similarity.
  - Detection is a heuristic (edge density + temporal stability), not a
    trained model -- it can be fooled by a static logo/watermark (also
    high density, low variance) or miss a caption style with unusually
    thin/low-contrast text.
None of these need to be solved before this is useful; they're places a
person may need to intervene (pick a different sample interval, or a
future manual region override) rather than bugs.
"""
import os
import subprocess
import tempfile

import cv2
import numpy as np

import ocr as ocr_module


def extract_frames(video_path: str, out_dir: str, interval_sec: float = 1.0):
    """
    Samples one frame every `interval_sec` seconds via ffmpeg's own fps
    filter (which decodes once, straight through -- far faster than
    seeking frame-by-frame from Python for a multi-hour file), saved as
    numbered PNGs in out_dir. Returns [(timestamp_seconds, path), ...] in
    order.
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%06d.png")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps=1/{interval_sec}",
           "-q:v", "2", pattern]
    subprocess.run(cmd, check=True, capture_output=True)
    frames = sorted(f for f in os.listdir(out_dir) if f.startswith("frame_"))
    return [(i * interval_sec, os.path.join(out_dir, f)) for i, f in enumerate(frames)]


def _edge_row_profile(gray: np.ndarray) -> np.ndarray:
    """Canny edge density per row. Burned-in captions are almost always
    rendered with a lot of local contrast (white fill / black stroke, or
    the reverse) to stay readable over arbitrary video underneath, which
    shows up as a strong, horizontally-continuous band of edges."""
    edges = cv2.Canny(gray, 80, 160)
    return edges.mean(axis=1)


def detect_caption_band(frame_paths, sample_count: int = 12, band_frac: float = 0.12):
    """
    Looks at up to `sample_count` frames spread evenly across the video and
    scores every row band on two things: how much edge density it has
    (text has a lot of edges) and how STABLE that edge pattern is across
    samples (a caption sits still and only changes when the line changes;
    ordinary video content's edges shift constantly as things move).
    Density alone would false-positive on any busy background; stability
    alone would false-positive on a static logo/letterbox -- combining
    them favors an actual recurring caption over either.

    Returns (y_start, y_end) as fractions (0.0-1.0) of frame height, so
    it applies regardless of the video's actual resolution. Returns None
    if fewer than 2 usable frames are available (variance needs at least
    2 samples) -- the caller falls back to a fixed default band then.
    """
    if len(frame_paths) < 2:
        return None
    idxs = sorted(set(np.linspace(0, len(frame_paths) - 1,
                                   min(sample_count, len(frame_paths))).astype(int)))
    profiles = []
    height = None
    for i in idxs:
        img = cv2.imread(frame_paths[i], cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        if height is None:
            height = img.shape[0]
        elif img.shape[0] != height:
            img = cv2.resize(img, (img.shape[1], height))
        profiles.append(_edge_row_profile(img))
    if len(profiles) < 2 or not height:
        return None

    stack = np.stack(profiles)
    mean_density = stack.mean(axis=0)
    variance = stack.var(axis=0)
    # Normalized so neither term dominates purely from differing scale.
    score = (mean_density / (mean_density.max() + 1e-6)
              - 0.5 * (variance / (variance.max() + 1e-6)))

    band_rows = max(1, int(height * band_frac))
    # A sliding-window sum via cumsum -- O(height) instead of O(height^2)
    # for what would otherwise be a lot of frame heights over long videos.
    cumsum = np.concatenate([[0.0], np.cumsum(score)])
    window_sums = cumsum[band_rows:] - cumsum[:-band_rows]
    best_start = int(np.argmax(window_sums))
    return (best_start / height, (best_start + band_rows) / height)


def _ocr_frame_region(path: str, region, lang: str, backend: str) -> str:
    img = cv2.imread(path)
    h, _w = img.shape[:2]
    y0, y1 = int(region[0] * h), int(region[1] * h)
    crop = img[y0:y1, :]
    fd, crop_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cv2.imwrite(crop_path, crop)
        if backend == "paddle":
            return ocr_module.extract_text_paddle(crop_path).strip()
        return ocr_module.extract_text_tesseract(crop_path, lang=lang).strip()
    finally:
        os.unlink(crop_path)


def _normalize_for_compare(text: str) -> str:
    """OCR noise (stray punctuation, whitespace) shouldn't split one real
    caption into several -- consecutive frames are compared on this
    loosely-cleaned form, not the raw text."""
    return "".join(text.split())


def dedupe_into_cues(timed_texts, interval_sec: float, min_chars: int = 1):
    """
    timed_texts: [(timestamp, text), ...] in order, one OCR result per
    sampled frame. Collapses a run of consecutive frames showing the same
    (normalized) text into a single cue spanning from the first frame that
    showed it to the last -- otherwise a caption on screen for 10 sampled
    frames would emit 10 near-identical subtitle lines instead of one.
    Blank/whitespace-only OCR results end the current cue without starting
    a new one (there's no caption on screen at that frame).
    """
    cues = []
    current_text = None
    current_start = None
    current_end = None

    def flush():
        if current_text and len(_normalize_for_compare(current_text)) >= min_chars:
            cues.append({"start": current_start, "end": current_end + interval_sec,
                        "text": current_text})

    for ts, text in timed_texts:
        text = (text or "").strip()
        norm = _normalize_for_compare(text)
        cur_norm = _normalize_for_compare(current_text or "")
        if norm == cur_norm and norm != "":
            current_end = ts
        else:
            flush()
            current_text = text
            current_start = ts
            current_end = ts
    flush()
    return cues


def extract_hardsub_subtitles(video_path: str, language: str = "zh",
                               sample_interval: float = 1.0,
                               ocr_backend: str = "tesseract",
                               progress_cb=None, tmp_dir=None):
    """
    Full pipeline: sample frames, auto-detect the caption band, OCR each
    sampled frame in that band, collapse the results into timed cues.
    Returns the same [{"start", "end", "text"}, ...] shape that
    core.transcribe_for_timing() returns, so it drops straight into the
    existing Lines/alignment/translate pipeline as if it were a
    transcript -- the rest of the app doesn't need to know the timing
    came from OCR instead of speech recognition.
    """
    lang = ocr_module.TESSERACT_LANG.get(language, "chi_sim")
    with tempfile.TemporaryDirectory(dir=tmp_dir) as frame_dir:
        frames = extract_frames(video_path, frame_dir, interval_sec=sample_interval)
        if not frames:
            return []

        band = detect_caption_band([p for _, p in frames])
        if band is None:
            band = (0.75, 1.0)  # fallback: bottom quarter, the common default

        timed_texts = []
        total = len(frames)
        for i, (ts, path) in enumerate(frames):
            text = _ocr_frame_region(path, band, lang, ocr_backend)
            timed_texts.append((ts, text))
            if progress_cb:
                progress_cb((i + 1) / total)

    return dedupe_into_cues(timed_texts, sample_interval)
