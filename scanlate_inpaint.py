"""
scanlate_inpaint.py -- erase the original text inside a bubble or mask
(LaMa-manga model, or plain OpenCV inpainting as the fallback).
"""

import os
from typing import Any

import numpy as np

import model_pins
import scanlate_tuning


_LAMA_ML_REPO = "mayocream/lama-manga"
_LAMA_ML_FILE = "lama-manga.safetensors"
_LAMA_BLOCKS = 18

# The generator keeps 64-channel float32 activations at full resolution, so a
# region of a few megapixels (a user-drawn box, a tall webtoon strip) needs
# gigabytes. Past this size the OpenCV fallback is used instead.
_ML_MAX_PIXELS = 3_000_000

# Dark-on-light lettering is every pixel under this share of the paper's gray.
_LETTERING_CUTOFF_OF_PAPER = 0.8

# Set when the checkpoint is present but unusable (corrupt, wrong shape), so one
# bad ~200 MB file is not re-read for every region of every page.
_lama_load_error = None


def lama_ml_weights_cached() -> bool:
    """Same idea as bubble_ml_weights_cached(), for the LaMa-manga
    inpainting checkpoint."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    hit = try_to_load_from_cache(repo_id=_LAMA_ML_REPO, filename=_LAMA_ML_FILE,
                                 revision=model_pins.revision(_LAMA_ML_REPO))
    return isinstance(hit, str) and os.path.exists(hit)


class InpaintModelUnavailable(RuntimeError):
    """The ML inpainting backend couldn't be loaded or run. Carries
    whether plain OpenCV inpainting already ran as a fallback, matching
    BubbleModelUnavailable's shape so callers can handle both the same
    way."""

    # Set only when the OpenCV fallback already produced an image.
    result: Any

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv


def _local_conv(conv):
    """A layer's conv from its local channels, which every LaMa layer that has
    outputs on that side also has inputs for."""
    if conv is None:
        raise TypeError("a layer with outputs needs local input channels")
    return conv


def _build_lama_generator():
    """The FFC-ResNet generator of big-lama-manga (18 residual blocks, 512
    features, 4 input channels: masked RGB plus the mask), with module names
    that match the checkpoint's `model.<n>...` keys so its weights load
    unchanged. The earlier hand-written layout (9 blocks, other key names)
    matched almost none of them, so the ML path could never run.

    Imports torch lazily so the OpenCV path never needs it installed.
    """
    import torch
    import torch.nn as nn

    class FourierUnit(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.conv_layer = nn.Conv2d(channels * 2, channels * 2, 1, bias=False)
            self.bn = nn.BatchNorm2d(channels * 2)

        def forward(self, x):
            b, _, h, w = x.shape
            f = torch.fft.rfftn(x, dim=(-2, -1), norm="ortho")
            f = torch.stack((f.real, f.imag), dim=-1).permute(0, 1, 4, 2, 3)
            f = f.contiguous().view(b, -1, h, w // 2 + 1)
            f = torch.relu(self.bn(self.conv_layer(f)))
            f = f.view(b, -1, 2, h, w // 2 + 1).permute(0, 1, 3, 4, 2).contiguous()
            f = torch.complex(f[..., 0], f[..., 1])
            return torch.fft.irfftn(f, s=(h, w), dim=(-2, -1), norm="ortho")

    class SpectralTransform(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.conv1 = nn.Sequential(
                nn.Conv2d(in_ch, out_ch // 2, 1, bias=False),
                nn.BatchNorm2d(out_ch // 2), nn.ReLU(inplace=True))
            self.fu = FourierUnit(out_ch // 2)
            self.conv2 = nn.Conv2d(out_ch // 2, out_ch, 1, bias=False)

        def forward(self, x):
            x = self.conv1(x)
            return self.conv2(x + self.fu(x))

    class FFC(nn.Module):
        def __init__(self, in_ch, out_ch, kernel, ratio_in, ratio_out, stride=1, padding=1):
            super().__init__()
            in_g = int(in_ch * ratio_in)
            in_l = in_ch - in_g
            out_g = int(out_ch * ratio_out)
            out_l = out_ch - out_g

            def conv(a, b):
                return (nn.Conv2d(a, b, kernel, stride, padding, bias=False,
                                  padding_mode="reflect") if a and b else None)

            self.convl2l = conv(in_l, out_l)
            self.convl2g = conv(in_l, out_g)
            self.convg2l = conv(in_g, out_l)
            self.convg2g = SpectralTransform(in_g, out_g) if in_g and out_g else None
            self.out_l, self.out_g = out_l, out_g

        def forward(self, x):
            x_l, x_g = x if isinstance(x, tuple) else (x, 0)
            out_l = out_g = 0
            if self.out_l:
                out_l = _local_conv(self.convl2l)(x_l) + (self.convg2l(x_g) if self.convg2l is not None else 0)
            if self.out_g:
                out_g = _local_conv(self.convl2g)(x_l) + (self.convg2g(x_g) if self.convg2g is not None else 0)
            return out_l, out_g

    class FFCBNAct(nn.Module):
        def __init__(self, in_ch, out_ch, kernel, ratio_in, ratio_out, stride=1, padding=1):
            super().__init__()
            self.ffc = FFC(in_ch, out_ch, kernel, ratio_in, ratio_out, stride, padding)
            self.bn_l = nn.BatchNorm2d(self.ffc.out_l) if self.ffc.out_l else nn.Identity()
            self.bn_g = nn.BatchNorm2d(self.ffc.out_g) if self.ffc.out_g else nn.Identity()

        def forward(self, x):
            out_l, out_g = self.ffc(x)
            return (torch.relu(self.bn_l(out_l)) if self.ffc.out_l else 0,
                    torch.relu(self.bn_g(out_g)) if self.ffc.out_g else 0)

    class ResBlock(nn.Module):
        def __init__(self, channels, ratio=0.75):
            super().__init__()
            self.conv1 = FFCBNAct(channels, channels, 3, ratio, ratio)
            self.conv2 = FFCBNAct(channels, channels, 3, ratio, ratio)

        def forward(self, x):
            out_l, out_g = self.conv2(self.conv1(x))
            return x[0] + out_l, x[1] + out_g

    class JoinLocalGlobal(nn.Module):
        def forward(self, x):
            return torch.cat(x, dim=1) if isinstance(x, tuple) else x

    layers = [nn.ReflectionPad2d(3), FFCBNAct(4, 64, 7, 0, 0, padding=0),
              FFCBNAct(64, 128, 3, 0, 0, stride=2), FFCBNAct(128, 256, 3, 0, 0, stride=2),
              FFCBNAct(256, 512, 3, 0, 0.75, stride=2)]
    layers += [ResBlock(512) for _ in range(_LAMA_BLOCKS)] + [JoinLocalGlobal()]
    ch = 512
    for _ in range(3):
        layers += [nn.ConvTranspose2d(ch, ch // 2, 3, 2, 1, output_padding=1),
                   nn.BatchNorm2d(ch // 2), nn.ReLU(True)]
        ch //= 2
    layers += [nn.ReflectionPad2d(3), nn.Conv2d(64, 3, 7), nn.Sigmoid()]
    return nn.Sequential(*layers)


def _load_lama_generator(hf_token: str | None = None, allow_download: bool = True):
    """Loads the LaMa-manga checkpoint into _build_lama_generator()'s
    architecture, downloading it first when `allow_download` and it isn't
    cached. "auto" passes allow_download=False: it only picks the ML backend
    when the weights are already cached, so it must not make a network request
    (or fetch a newer revision) in the middle of a render. Raises RuntimeError
    -- caught by inpaint_region()/inpaint_mask_region() and turned into
    InpaintModelUnavailable -- if most of the checkpoint's weights don't match,
    rather than silently returning a near-random model; an unusable checkpoint
    is remembered for the rest of the process."""
    global _lama_model, _lama_load_error
    if "_lama_model" in globals():
        return globals()["_lama_model"]
    if _lama_load_error is not None:
        raise RuntimeError(_lama_load_error)

    import os as _os
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file as _load_safetensors

    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")

    from lib import model_offline
    # A run started away from the PC loads the cached file only; a server
    # thread cannot use the process-wide offline switch (lib/model_offline.py).
    local_only = model_offline.active()
    offline = {"local_files_only": True} if (local_only or not allow_download) else {}
    try:
        with model_pins.fetching(_LAMA_ML_REPO):
            ckpt_path = hf_hub_download(repo_id=_LAMA_ML_REPO, filename=_LAMA_ML_FILE,
                                        revision=model_pins.revision(_LAMA_ML_REPO),
                                        token=_tok, etag_timeout=30, **offline)
    except Exception as exc:
        if local_only:
            raise RuntimeError(model_offline.NOT_DOWNLOADED_MESSAGE) from exc
        raise
    try:
        # The checkpoint was saved from an nn.Sequential held as `model`.
        state_dict = {k.removeprefix("model."): v
                      for k, v in _load_safetensors(ckpt_path).items()}

        generator = _build_lama_generator()
        own_keys = list(generator.state_dict().keys())
        missing, unexpected = generator.load_state_dict(state_dict, strict=False)
        if len(missing) > len(own_keys) * 0.1:
            raise RuntimeError(
                f"LaMa-manga checkpoint doesn't match the expected generator shape "
                f"({len(missing)}/{len(own_keys)} weights unmatched, "
                f"{len(unexpected)} unexpected keys in the checkpoint) -- "
                f"_build_lama_generator() likely needs updating against this "
                f"checkpoint's real state_dict key names."
            )
    except Exception as exc:
        _lama_load_error = f"{type(exc).__name__}: {exc}"
        raise
    generator.eval()
    globals()["_lama_model"] = generator
    return generator


def _run_ml_inpaint(roi_bgr, mask_u8, hf_token: str | None = None, allow_download: bool = True):
    """Runs LaMa-manga inpainting on one region-of-interest. roi_bgr: an
    OpenCV BGR array. mask_u8: a same-size uint8 array, nonzero = erase
    this pixel. Returns a BGR array the same size as roi_bgr, with only
    the masked pixels replaced -- everywhere else stays byte-identical
    to the input, same "only touch what was actually erased" discipline
    as the OpenCV path below."""
    if roi_bgr.shape[0] * roi_bgr.shape[1] > _ML_MAX_PIXELS:
        raise ValueError(f"the region is over {_ML_MAX_PIXELS // 1_000_000} megapixels, "
                         f"too large for the model")
    import numpy as np
    import torch
    import cv2

    generator = _load_lama_generator(hf_token=hf_token, allow_download=allow_download)
    rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB).astype("float32") / 255.0
    mask = (mask_u8 > 0).astype("float32")
    # Zero out the masked area in the image channel first -- otherwise the
    # model can "peek" at the very pixels it's meant to be reconstructing.
    rgb_masked = rgb * (1 - mask[..., None])
    inp = np.concatenate([rgb_masked, mask[..., None]], axis=-1)
    tensor = torch.from_numpy(inp).permute(2, 0, 1).unsqueeze(0)

    # Three stride-2 downsamples need both spatial dims divisible by 8.
    h, w = tensor.shape[-2:]
    pad_h, pad_w = (-h) % 8, (-w) % 8
    if pad_h or pad_w:
        tensor = torch.nn.functional.pad(tensor, (0, pad_w, 0, pad_h), mode="reflect")

    with torch.no_grad():
        out = generator(tensor)[0]
    out = out[:, :h, :w].clamp(0, 1).permute(1, 2, 0).numpy()
    out_bgr = cv2.cvtColor((out * 255).astype("uint8"), cv2.COLOR_RGB2BGR)

    mask3 = np.repeat((mask_u8 > 0)[..., None], 3, axis=2)
    return np.where(mask3, out_bgr, roi_bgr)


def background_is_dark(gray_roi) -> bool:
    """True when most of the region is dark, i.e. the lettering is the light part."""
    return float(np.median(gray_roi)) < 100


def _lettering_mask(gray_roi):
    """uint8 mask (255 = erase) of the lettering in a gray crop, grown past
    its anti-aliased edge: a fixed gray cut-off left the pale strokes and the
    halo round each stroke behind as ghost text under the English. The cut-off
    follows the paper's own brightness, so lettering on tinted paper is caught
    too."""
    import cv2
    if background_is_dark(gray_roi):
        # Light lettering on a dark balloon: masking the dark pixels would
        # erase the whole balloon and smear the surrounding art across it.
        _, mask = cv2.threshold(gray_roi, 150, 255, cv2.THRESH_BINARY)
    else:
        paper = float(np.median(gray_roi))
        cutoff = int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
        _, mask = cv2.threshold(gray_roi, cutoff, 255, cv2.THRESH_BINARY_INV)
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2)


_ART_MIN_EDGE_MARK_SHARE = 0.08


def _drop_marks_running_off_edge(mask) -> None:
    """Clears, in place, the mask components that touch the crop's edge and
    cover a sizeable share of it. Small ones stay: a stroke can end at the
    edge of a tight box."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    rh, rw = mask.shape[:2]
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if (x == 0 or y == 0 or x + w == rw or y + h == rh) and area >= _ART_MIN_EDGE_MARK_SHARE * rh * rw:
            mask[labels == i] = 0


# A mark this large in both directions yet this hollow is a frame or ornament
# round the lettering, not the lettering itself.
_FRAME_MIN_SPAN = 0.35
_FRAME_MAX_FILL = 0.25


def _drop_hollow_frames(mask) -> None:
    """Clears, in place, the mask components that span much of the crop in both
    directions but are mostly empty: a caption frame's border and ornaments.
    Lettering lines are small in one direction or dense, so they stay. Erasing a
    frame left a pale patch with the lettering's ghost where the frame had been."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    rh, rw = mask.shape[:2]
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if w >= _FRAME_MIN_SPAN * rw and h >= _FRAME_MIN_SPAN * rh and area <= _FRAME_MAX_FILL * w * h:
            mask[labels == i] = 0


_ART_FILL_CONTEXT_PX = 60
# Outlined lettering is white or pale; bright pixels that are strongly coloured
# are artwork (flames, glare) and stay out of the lettering mask, so painting the
# lettering out cannot blank the art round it.
_ART_FILL_SATURATION = 120
# Median saturation from which a box counts as coloured art. Its flat-tone fill
# would show as a smudge on that texture, and its median brightness can read as
# light even though the lettering is pale outlined text, so LaMa gets the mask.
_COLOURED_ART_MEDIAN_SATURATION = 100


def _fill_enclosed_holes(mask):
    """`mask` with every 0-region that does not reach its edge set to 255: the
    inside of a letter whose outline is the only part that was masked. The
    border is padded first so the flood starts on background even when the
    corner pixel is itself masked."""
    import cv2
    h, w = mask.shape[:2]
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(padded, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 255)
    return cv2.bitwise_or(mask, cv2.bitwise_not(padded[1:-1, 1:-1]))


def _pale_pixels(crop):
    """Bool array: bright, weakly coloured pixels of a BGR crop, i.e. the pale
    outline and glow of lettering rather than the artwork."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    return (gray > 150) & (saturation <= _ART_FILL_SATURATION)


def is_coloured_art(crop) -> bool:
    import cv2
    return float(np.median(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1])) > _COLOURED_ART_MEDIAN_SATURATION


def _dark_art_lettering_mask(crop):
    """uint8 mask (255 = erase) of outlined lettering over dark art. The bright
    outline is the only part a brightness cut-off finds, so the fill inside
    each letter is recovered by flooding from the corner and everything within
    a few pixels is taken too, which removes the glow round the strokes."""
    import cv2
    core = _pale_pixels(crop).astype(np.uint8) * 255
    core = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=2)
    core = _fill_enclosed_holes(core)
    core = cv2.morphologyEx(core, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    return cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (33, 33)))


def _dark_art_fill(img, x: int, y: int, bw: int, bh: int):
    """The box's crop with its lettering replaced by the colours of the art
    round it, or None when it holds no pale lettering.
    TELEA and LaMa both pull the stroke glow into the fill and leave a grey
    smudge on dark art; averaging only pixels well clear of the lettering
    gives the art's own tone."""
    import cv2
    crop = img[y:y + bh, x:x + bw]
    if not _pale_pixels(crop).any():
        return None
    mask_box = _dark_art_lettering_mask(crop)
    ih, iw = img.shape[:2]
    x0, y0 = max(0, x - _ART_FILL_CONTEXT_PX), max(0, y - _ART_FILL_CONTEXT_PX)
    x1, y1 = min(iw, x + bw + _ART_FILL_CONTEXT_PX), min(ih, y + bh + _ART_FILL_CONTEXT_PX)
    area = img[y0:y1, x0:x1].astype(np.float32)
    mask = np.zeros(area.shape[:2], np.uint8)
    mask[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = mask_box
    mask = _fill_enclosed_holes(mask)
    clear = (cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))) == 0)
    clear = clear.astype(np.float32)
    sigma = 15
    tone = cv2.GaussianBlur(area * clear[..., None], (0, 0), sigma) \
        / np.maximum(cv2.GaussianBlur(clear, (0, 0), sigma)[..., None], 1e-3)
    weight = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 4)[..., None]
    blended = (tone * weight + area * (1 - weight)).clip(0, 255).astype(np.uint8)
    return blended[y - y0:y - y0 + bh, x - x0:x - x0 + bw]


def _resolve_inpaint_backend(backend: str) -> str:
    if backend == "auto":
        return "ml" if lama_ml_weights_cached() else "cv"
    return backend


def _try_ml_inpaint(roi, mask, hf_token, backend):
    """(inpainted crop, None) from LaMa, or (None, InpaintModelUnavailable) when
    it can't run, so the caller finishes with OpenCV and still raises the error."""
    try:
        return _run_ml_inpaint(roi, mask, hf_token=hf_token, allow_download=backend == "ml"), None
    except ImportError as exc:
        error = InpaintModelUnavailable(
            "ML inpainting needs extra packages:\n"
            "    pip install torch safetensors huggingface_hub\n\n"
            "Falling back to plain OpenCV inpainting for this bubble."
        )
        error.__cause__ = exc
    except Exception as exc:
        error = InpaintModelUnavailable(
            f"LaMa-manga inpainting failed: {type(exc).__name__}: {exc}\n\n"
            "Falling back to plain OpenCV inpainting for this bubble."
        )
        error.__cause__ = exc
    return None, error


def _flatten_plain_paper(roi, inpainted, mask, inside):
    """`inpainted` with the inside of a plain white balloon set to its paper
    colour. The lettering mask misses the faint halo a JPEG leaves round each
    stroke, and inpainting only repaints the mask, so that halo showed as a
    ghost under the English. A balloon whose paper varies (tone, a gradient)
    is returned as it is, as is one too small to measure. The outline's own
    edge is kept out by eroding the inside first."""
    import cv2
    core = cv2.erode(inside.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    paper = core & (mask == 0)
    if int(paper.sum()) < 50:
        return inpainted
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    values = gray[paper]
    # The halo is darker than the paper, so the typical value is the median;
    # the spread is taken round it over the lighter half to ignore that halo.
    median = float(np.median(values))
    light = values[values >= median]
    if float(light.std()) > scanlate_tuning.INPAINT_FLAT_PAPER_STD:
        return inpainted
    out = inpainted.copy()
    out[core] = np.median(roi[paper], axis=0).astype(inpainted.dtype)
    return out


def inpaint_region(image_path: str, box: dict, out_path: str | None = None, padding: int = 4,
                    backend: str = "auto", hf_token: str | None = None, image=None, inside=None,
                    spare_art: bool = False, on_art: bool = False):
    """Removes text within `box` so the translated text has a clean
    background. Returns the path to the (possibly newly-created) cleaned
    image; if out_path is None, overwrites nothing and returns an image
    array instead.

    backend='cv' (free, plain OpenCV inpainting), 'ml' (LaMa-manga,
    shape-aware, not just a rectangular inset), or
    'auto' (default) -- use LaMa-manga if its weights
    are already cached locally, OpenCV otherwise. If the ML backend is
    picked (explicitly or via auto) but can't actually run, this falls
    back to OpenCV inpainting and raises InpaintModelUnavailable with
    the cleaned image still attached, same fallback shape as
    detect_bubbles().

    image: the page already decoded as a BGR array. It is erased in place
    (image_path isn't read), so a render can clean every bubble on one
    decoded page and encode the result once.

    inside: optional boolean (box h, box w) array, True inside the balloon's
    outline (scanlate_detect.bubble_erase_mask()). Only marks inside
    it are erased: the box's corners hold the balloon's outline and the art
    around it, which erasing every dark pixel in the rectangle used to wipe
    into a white block.

    spare_art: for a balloon whose outline wasn't found (`inside` is None),
    leave dark marks that run off the box edge. Lettering sits inside the
    balloon, so a large mark touching the edge is the art the balloon is
    drawn over, and erasing it smears white across the box.

    on_art: the region is a sign, lettering on the artwork rather than in a
    balloon or box: pale lettering on dark or coloured art is painted out with
    the art's own tone or a full lettering mask, since an outline-only mask
    left the letters' fill and glow behind as ghost text. A dark box that is a
    balloon or narration panel must not take the colours round it, so only
    signs opt in."""
    import cv2
    img = image if image is not None else cv2.imread(image_path)
    h, w = img.shape[:2]
    x = max(0, box["x"] - padding)
    y = max(0, box["y"] - padding)
    bw = min(w - x, box["w"] + 2 * padding)
    bh = min(h - y, box["h"] + 2 * padding)

    roi = img[y:y + bh, x:x + bw]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = _lettering_mask(gray_roi)
    if inside is not None:
        keep = np.zeros(mask.shape, dtype=bool)
        oy, ox = box["y"] - y, box["x"] - x
        part = inside[max(0, -oy):, max(0, -ox):]
        part = part[:bh - max(oy, 0), :bw - max(ox, 0)]
        keep[max(oy, 0):max(oy, 0) + part.shape[0], max(ox, 0):max(ox, 0) + part.shape[1]] = part
        mask[~keep] = 0
    elif spare_art:
        _drop_marks_running_off_edge(mask)
    elif on_art:
        _drop_hollow_frames(mask)

    inpainted_roi = None
    if inside is None and on_art and is_coloured_art(roi) and _pale_pixels(roi).any():
        # An outline-only mask left the letters' fill and glow behind as ghosts.
        mask = _dark_art_lettering_mask(roi)
    elif inside is None and (spare_art or on_art) and background_is_dark(gray_roi):
        inpainted_roi = _dark_art_fill(img, x, y, bw, bh)
    resolved = "cv" if inpainted_roi is not None else _resolve_inpaint_backend(backend)
    fallback_error = None
    if resolved == "ml":
        inpainted_roi, fallback_error = _try_ml_inpaint(roi, mask, hf_token, backend)

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    if inside is not None and not background_is_dark(gray_roi):
        inpainted_roi = _flatten_plain_paper(roi, inpainted_roi, mask, keep)
    img[y:y + bh, x:x + bw] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
        result = out_path
    else:
        result = img

    if fallback_error is not None:
        fallback_error.fell_back_to_cv = True
        # Attach the already-produced result so a caller that wants it
        # doesn't have to redo the OpenCV pass itself.
        fallback_error.result = result
        raise fallback_error
    return result


_ERASE_CONTEXT_PX = 24


def erase_box(img, box: dict) -> None:
    """Paints out every pixel of `box` on `img` (BGR, in place), for a logo or
    stamp. OpenCV, from a margin of surrounding art: the LaMa-manga model is
    trained on balloons and fills a large patch of artwork with flat pale
    grey, and OpenCV given only the box has no pixels to draw from."""
    import cv2
    h, w = img.shape[:2]
    x0, y0 = max(0, box["x"] - _ERASE_CONTEXT_PX), max(0, box["y"] - _ERASE_CONTEXT_PX)
    x1 = min(w, box["x"] + box["w"] + _ERASE_CONTEXT_PX)
    y1 = min(h, box["y"] + box["h"] + _ERASE_CONTEXT_PX)
    roi = img[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    mask[box["y"] - y0:box["y"] - y0 + box["h"], box["x"] - x0:box["x"] - x0 + box["w"]] = 255
    roi[:] = cv2.inpaint(roi, mask, 7, cv2.INPAINT_TELEA)


def inpaint_mask_region(image_path: str, mask, out_path: str | None = None, padding: int = 4,
                         backend: str = "auto", hf_token: str | None = None):
    """Manual erase/heal brush: inpaints exactly the
    pixels the person painted, independent of any detected bubble box --
    a sound effect, background text, or a stray detection artifact the
    auto/manual bubble tools never touch.

    mask: a 2D array the same height/width as the source image; any
    nonzero pixel is erased (this is the raw brush-stroke mask -- unlike
    inpaint_region(), there's no "text is dark pixels inside a light
    bubble" heuristic here, because the person is manually choosing what
    to remove, not detecting text). backend/hf_token: same as
    inpaint_region(). Raises InpaintModelUnavailable the same way, with
    the OpenCV-inpainted result still attached, if the ML backend can't
    run."""
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    mask = np.asarray(mask)
    if mask.shape[:2] != (h, w):
        raise ValueError(
            f"mask shape {mask.shape[:2]} doesn't match the image {(h, w)}")

    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        # Nothing painted -- return the image unchanged.
        if out_path:
            cv2.imwrite(out_path, img)
            return out_path
        return img

    x0, x1 = max(0, int(xs.min()) - padding), min(w, int(xs.max()) + 1 + padding)
    y0, y1 = max(0, int(ys.min()) - padding), min(h, int(ys.max()) + 1 + padding)
    roi = img[y0:y1, x0:x1]
    roi_mask = (mask[y0:y1, x0:x1] > 0).astype("uint8") * 255
    roi_mask = cv2.dilate(roi_mask, np.ones((3, 3), np.uint8), iterations=1)

    resolved = _resolve_inpaint_backend(backend)
    inpainted_roi = None
    fallback_error = None
    if resolved == "ml":
        try:
            inpainted_roi = _run_ml_inpaint(roi, roi_mask, hf_token=hf_token,
                                            allow_download=backend == "ml")
        except ImportError as exc:
            fallback_error = InpaintModelUnavailable(
                "ML inpainting needs extra packages:\n"
                "    pip install torch safetensors huggingface_hub\n\n"
                "Falling back to plain OpenCV inpainting for this brush stroke."
            )
            fallback_error.__cause__ = exc
        except Exception as exc:
            fallback_error = InpaintModelUnavailable(
                f"LaMa-manga inpainting failed: {type(exc).__name__}: {exc}\n\n"
                "Falling back to plain OpenCV inpainting for this brush stroke."
            )
            fallback_error.__cause__ = exc

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, roi_mask, 5, cv2.INPAINT_TELEA)
    img[y0:y1, x0:x1] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
        result = out_path
    else:
        result = img

    if fallback_error is not None:
        fallback_error.fell_back_to_cv = True
        fallback_error.result = result
        raise fallback_error
    return result
