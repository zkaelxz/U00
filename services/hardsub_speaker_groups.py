"""
services/hardsub_speaker_groups.py -- splits a picture of the subtitle band into one group per speaker.
Dramas that burn in one colour and one corner per speaker are read group by group, so a quiet speaker is
never lost behind a loud one. Local and cheap: no model, only pixel colour and position. UI-free.
"""
from dataclasses import dataclass

import numpy
from PIL import Image

# A pixel is subtitle text when it is light (value) and either clearly coloured or nearly white; the dark
# outline and the picture behind it fall under both tests.
MIN_TEXT_VALUE = 0.62
MIN_COLOUR_SATURATION = 0.45
MAX_WHITE_SATURATION = 0.22
HUE_BINS = 12
# Pixel runs in the same colour further apart than this share of the picture's width are different
# speakers (their text sits in opposite corners); gaps between characters and words are far smaller.
SPEAKER_GAP_FRACTION = 0.08
# Below this share of the band's pixels a colour patch is noise (a bright logo edge), not a caption.
MIN_GROUP_PIXEL_FRACTION = 0.0006
GROUP_PADDING = 8
HUE_NAMES = ("red", "orange", "yellow", "lime", "green", "teal", "cyan", "azure", "blue", "violet", "magenta", "rose")
_SIDES = ("left", "centre", "right")


@dataclass
class Group:
    key: str          # e.g. "yellow-right": stable across pictures of the same video
    box: tuple        # (left, top, right, bottom) in pixels of the band
    pixels: int


def _colour_bins(rgb: numpy.ndarray) -> numpy.ndarray:
    """Per pixel: -1 for not text, 0..HUE_BINS-1 for a hue, HUE_BINS for white."""
    flat = rgb.reshape(-1, 3) / 255.0
    value = flat.max(axis=1)
    low = flat.min(axis=1)
    saturation = numpy.where(value > 0, (value - low) / numpy.maximum(value, 1e-9), 0.0)
    bins = numpy.full(len(flat), -1, dtype=numpy.int64)
    bins[(value >= MIN_TEXT_VALUE) & (saturation <= MAX_WHITE_SATURATION)] = HUE_BINS
    coloured = (value >= MIN_TEXT_VALUE) & (saturation >= MIN_COLOUR_SATURATION)
    red, green, blue = flat[coloured].T
    span = (value[coloured] - low[coloured])
    hue = numpy.where(value[coloured] == red, ((green - blue) / span) % 6,
                      numpy.where(value[coloured] == green, (blue - red) / span + 2, (red - green) / span + 4)) / 6
    bins[coloured] = (hue * HUE_BINS + 0.5).astype(numpy.int64) % HUE_BINS
    return bins.reshape(rgb.shape[:2])


def _runs(columns: numpy.ndarray, max_gap: int) -> list:
    """[(first, last)] column ranges that hold pixels, merging ranges closer than `max_gap`."""
    xs = numpy.flatnonzero(columns)
    if len(xs) == 0:
        return []
    runs, start, prev = [], xs[0], xs[0]
    for x in xs[1:]:
        if x - prev > max_gap:
            runs.append((int(start), int(prev)))
            start = x
        prev = x
    runs.append((int(start), int(prev)))
    return runs


def split_band(image: Image.Image) -> list:
    """The speaker groups in a subtitle band, left to right. A group is one text colour in one stretch of the
    band; an empty or text-free band gives no groups."""
    rgb = numpy.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    bins = _colour_bins(rgb)
    min_pixels = max(1, int(height * width * MIN_GROUP_PIXEL_FRACTION))
    max_gap = max(1, int(width * SPEAKER_GAP_FRACTION))
    groups = []
    for colour in range(HUE_BINS + 1):
        mask = bins == colour
        if mask.sum() < min_pixels:
            continue
        for first, last in _runs(mask.any(axis=0), max_gap):
            part = mask[:, first:last + 1]
            count = int(part.sum())
            if count < min_pixels:
                continue
            rows = numpy.flatnonzero(part.any(axis=1))
            centre = (first + last) / 2 / width
            side = _SIDES[min(2, int(centre * 3))]
            name = "white" if colour == HUE_BINS else HUE_NAMES[colour]
            box = (max(0, first - GROUP_PADDING), max(0, int(rows[0]) - GROUP_PADDING),
                   min(width, last + 1 + GROUP_PADDING), min(height, int(rows[-1]) + 1 + GROUP_PADDING))
            groups.append(Group(f"{name}-{side}", box, count))
    return sorted(groups, key=lambda g: g.box[0])
