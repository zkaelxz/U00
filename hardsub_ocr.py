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
  - Detection is a heuristic (edge density + temporal stability), not a
    trained model -- it can be fooled by a static logo/watermark (also
    high density, low variance) or miss a caption style with unusually
    thin/low-contrast text.
  - A stability filter (dedupe_into_cues' min_consecutive_samples) means a
    caption that's only ever sampled once -- shorter than one sample
    interval, or cut off by the video ending mid-caption -- gets dropped
    as indistinguishable from a one-frame misread/transition-frame blip.
    Lowering the sample interval catches shorter captions without
    weakening the filter.
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


def detect_caption_band(frame_paths, sample_count: int = 12, band_frac: float = 0.12,
                         pad_frac: float = 0.6):
    """
    Looks at up to `sample_count` frames spread evenly across the video and
    scores every row band on two things: how much edge density it has
    (text has a lot of edges) and how STABLE that edge pattern is across
    samples (a caption sits still and only changes when the line changes;
    ordinary video content's edges shift constantly as things move).
    Density alone would false-positive on any busy background; stability
    alone would false-positive on a static logo/letterbox -- combining
    them favors an actual recurring caption over either.

    The scoring window itself is only `band_frac` of the frame height,
    picked at whichever position scores highest -- for a caption that's
    genuinely taller than that window (a stylized two-line caption is a
    common real case), the best-scoring position tends to settle over
    the busier of the two lines rather than spanning both, clipping the
    other one out of every OCR crop. `pad_frac` expands the returned
    band by that fraction of band_rows on each side after the position
    is picked, so a second line just above or below the highest-scoring
    stripe still ends up inside the crop -- padding with a bit of plain
    background costs OCR accuracy far less than cropping out real text
    does, so this errs toward including it.

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
    best_end = best_start + band_rows

    pad = int(band_rows * pad_frac)
    best_start = max(0, best_start - pad)
    best_end = min(height, best_end + pad)
    return (best_start / height, best_end / height)


def _upscale_for_ocr(crop: np.ndarray, min_height: int = 120, max_scale: float = 3.0) -> np.ndarray:
    """A caption band cropped from a small/compressed source (a downloaded
    livestream VOD is a common case) can end up too short for either OCR
    engine's own text detector to render fine stroke detail cleanly --
    confirmed by direct testing to be a real source of misreads on
    characters that differ by a single small stroke or radical (e.g. 妳
    vs 你, 挑 vs 跳). Upscaling before OCR gives the detector/recognizer
    more pixels to work with; only crops actually below `min_height` are
    touched; already-tall crops are returned unchanged rather than being
    blown up (and slowed down) for no benefit."""
    height = crop.shape[0]
    if height <= 0 or height >= min_height:
        return crop
    scale = min(max_scale, min_height / height)
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _ocr_frame_region(path: str, region, lang: str, backend: str, tesseract_cmd: str = None) -> str:
    img = cv2.imread(path)
    h, _w = img.shape[:2]
    y0, y1 = int(region[0] * h), int(region[1] * h)
    crop = _upscale_for_ocr(img[y0:y1, :])
    fd, crop_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cv2.imwrite(crop_path, crop)
        if backend == "paddle":
            return ocr_module.extract_text_paddle(crop_path).strip()
        return ocr_module.extract_text_tesseract(
            crop_path, lang=lang, tesseract_cmd=tesseract_cmd).strip()
    finally:
        os.unlink(crop_path)


def _normalize_for_compare(text: str) -> str:
    """OCR noise (stray punctuation, whitespace) shouldn't split one real
    caption into several -- consecutive frames are compared on this
    loosely-cleaned form, not the raw text."""
    return "".join(text.split())


def dedupe_into_cues(timed_texts, interval_sec: float, min_chars: int = 1,
                      min_consecutive: int = 1):
    """
    timed_texts: [(timestamp, text), ...] in order, one OCR result per
    sampled frame. Collapses a run of consecutive frames showing the same
    (normalized) text into a single cue spanning from the first frame that
    showed it to the last -- otherwise a caption on screen for 10 sampled
    frames would emit 10 near-identical subtitle lines instead of one.
    Blank/whitespace-only OCR results end the current cue without starting
    a new one (there's no caption on screen at that frame).

    min_consecutive: how many samples in a row must show the same new text
    before it's accepted as a real caption change, rather than one-frame
    OCR jitter (a single misread character) or a transition-frame flicker
    (a fade in/out) splitting one real caption into extra short adjacent
    cues -- confirmed by direct testing to happen on an otherwise-static
    caption. A frame that doesn't repeat is simply absorbed into whichever
    cue it interrupted rather than starting or ending one; the cue's own
    start time is still the first frame the settled text appeared on, not
    the confirmation frame, so this doesn't add latency to real captions.
    The default of 1 accepts every change immediately (the old behavior);
    extract_hardsub_subtitles calls this with a higher value. A blank
    result always ends the current cue right away regardless of this
    setting -- there's no ambiguity to wait out when the caption's just
    gone. Trade-off: a caption sampled only once (shorter than
    min_consecutive * interval_sec, including one cut off by the stream
    ending) is indistinguishable from a blip and gets dropped too.
    """
    cues = []
    confirmed_text = None
    confirmed_start = None
    confirmed_end = None
    confirmed_norm = ""
    pending_text = None
    pending_start = None
    pending_count = 0

    def flush():
        if confirmed_text and len(_normalize_for_compare(confirmed_text)) >= min_chars:
            cues.append({"start": confirmed_start, "end": confirmed_end + interval_sec,
                        "text": confirmed_text})

    for ts, text in timed_texts:
        text = (text or "").strip()
        norm = _normalize_for_compare(text)
        if norm == confirmed_norm:
            confirmed_end = ts
            pending_text, pending_count = None, 0
            continue

        pending_norm = _normalize_for_compare(pending_text or "")
        if pending_text is not None and norm == pending_norm:
            pending_count += 1
        else:
            pending_text, pending_start, pending_count = text, ts, 1

        if norm == "" or pending_count >= min_consecutive:
            flush()
            confirmed_text, confirmed_start, confirmed_end = pending_text, pending_start, ts
            confirmed_norm = _normalize_for_compare(confirmed_text or "")
            pending_text, pending_count = None, 0
    flush()
    return cues


def extract_hardsub_subtitles(video_path: str, language: str = "zh",
                               sample_interval: float = 1.0,
                               ocr_backend: str = "tesseract",
                               chinese_script: str = "simplified",
                               progress_cb=None, tmp_dir=None, tesseract_cmd: str = None,
                               min_consecutive_samples: int = 2):
    """
    Full pipeline: sample frames, auto-detect the caption band, OCR each
    sampled frame in that band, collapse the results into timed cues.
    Returns the same [{"start", "end", "text"}, ...] shape that
    core.transcribe_for_timing() returns, so it drops straight into the
    existing Lines/alignment/translate pipeline as if it were a
    transcript -- the rest of the app doesn't need to know the timing
    came from OCR instead of speech recognition.

    min_consecutive_samples defaults to 2, not dedupe_into_cues' own
    default of 1 -- see that function's docstring for what this trades
    off (a caption sampled only once reads as noise and gets dropped);
    2 is a reasonable default for the real failure this fixes (one-frame
    misreads/transition flicker splitting a real caption into extra
    cues), but pass 1 to disable the filter entirely if a source is
    dropping genuinely short captions because of it.
    """
    lang = ocr_module.resolve_tesseract_lang(language, chinese_script)
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
            text = _ocr_frame_region(path, band, lang, ocr_backend, tesseract_cmd=tesseract_cmd)
            timed_texts.append((ts, text))
            if progress_cb:
                progress_cb((i + 1) / total)

    return dedupe_into_cues(timed_texts, sample_interval,
                             min_consecutive=min_consecutive_samples)
