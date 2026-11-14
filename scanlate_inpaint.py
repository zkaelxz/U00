"""
scanlate_inpaint.py -- erase the original text inside a bubble or mask
(LaMa-manga model, or plain OpenCV inpainting as the fallback).
"""

import math
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
# The halo a stroke leaves is only slightly darker than the paper, so it is
# taken from a looser cut-off, and only where it touches lettering already
# masked: elsewhere it would be the art's own shading.
_LETTERING_HALO_OF_PAPER = 0.9
_LETTERING_HALO_GROW_ITERATIONS = 2
_LETTERING_FINAL_GROW_ITERATIONS = 2
# A halo that more than doubles the mask is shading, not lettering.
_LETTERING_HALO_MAX_GROWTH = 2.5

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


# The generator's copy on the GPU under "cuda", made the first time a region fits there. The
# CPU copy stays the master: a region that does not fit, or a GPU that fails, uses it instead.
# whisper_models.release_gpu_models() empties this with the app's other GPU models.
_lama_gpu: dict = {}
# Why the GPU was given up on for this process (a driver or CUDA fault, not a full GPU).
_lama_gpu_broken: list = []
# On a 3080 Ti a page's balloons took about 1.2 s on the CPU. The GPU needs the weights
# (about 200 MB) and CUDA's own share once, plus working memory for the full-resolution
# 64-channel maps, which grows with the region; these are deliberately generous so a region
# only goes to the GPU when it clearly fits beside whatever else is loaded there.
_GPU_FIRST_LOAD_MB = 800
_GPU_MB_PER_MEGAPIXEL = 3000
_GPU_MAX_PIXELS = 1_000_000


def _lama_on_gpu(generator, pixels: int, torch):
    """The generator's GPU copy when a region of `pixels` fits on a free enough GPU and
    Settings allows GPU use, else None (the region runs on the CPU)."""
    if _lama_gpu_broken or pixels > _GPU_MAX_PIXELS or not torch.cuda.is_available():
        return None
    from services import settings_service, vram_service
    if not settings_service.get("use_gpu"):
        return None
    need = (0 if "cuda" in _lama_gpu else _GPU_FIRST_LOAD_MB) + pixels / 1_000_000 * _GPU_MB_PER_MEGAPIXEL
    try:
        # Also honours Settings > Keep free memory for the GPU.
        vram_service.check_fits("LaMa-manga", required_mb=need)
    except Exception:
        return None
    if "cuda" not in _lama_gpu:
        import copy
        _lama_gpu["cuda"] = copy.deepcopy(generator).to("cuda")
    return _lama_gpu["cuda"]


def _generate(generator, tensor, torch):
    """The generator's output for `tensor`, on the GPU when it fits there (see _lama_on_gpu),
    as a CPU tensor either way."""
    with torch.no_grad():
        try:
            gpu = _lama_on_gpu(generator, tensor.shape[-2] * tensor.shape[-1], torch)
            if gpu is not None:
                return gpu(tensor.to("cuda"))[0].cpu()
        except Exception as exc:
            # A GPU that filled up meanwhile costs only this region; any other fault (a
            # broken driver, a failed copy) would repeat on every region, so the CPU is kept.
            if not isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())):
                _lama_gpu_broken.append(type(exc).__name__)
                _lama_gpu.clear()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        return generator(tensor)[0]


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

    out = _generate(generator, tensor, torch)
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
    too. On paper (not light lettering on a dark balloon) the pale halo round
    each stroke is added too: a 3x3 dilation left it, and on ornate narration
    panels it showed as ghost Korean under the English."""
    import cv2
    if background_is_dark(gray_roi):
        # Light lettering on a dark balloon: masking the dark pixels would
        # erase the whole balloon and smear the surrounding art across it.
        _, mask = cv2.threshold(gray_roi, 150, 255, cv2.THRESH_BINARY)
    else:
        paper = float(np.median(gray_roi))
        cutoff = int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
        _, mask = cv2.threshold(gray_roi, cutoff, 255, cv2.THRESH_BINARY_INV)
        return _with_halo(gray_roi, paper, cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2))
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2)


def is_art_mistaken_for_sign(gray_page, box: dict) -> bool:
    """True when `box`, a guessed sign, is artwork rather than lettering: the
    lettering mask would cover nearly all of it, so erasing would paint the art
    out (a leg became a smear). Only a low-confidence sign is judged; a box the
    person or the detector was sure of keeps the normal path, and so does a wide
    strip, which is a caption."""
    if box.get("kind") != "sign" or float(box.get("kind_confidence") or 0.0) > scanlate_tuning.ART_SIGN_MAX_CONFIDENCE:
        return False
    if box["w"] > scanlate_tuning.ART_SIGN_MAX_ASPECT * box["h"]:
        return False
    h, w = gray_page.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = gray_page[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return False
    return float((_lettering_mask(roi) > 0).mean()) > scanlate_tuning.ART_SIGN_MIN_MASK_SHARE


def _with_halo(gray_roi, paper: float, core_mask):
    """`core_mask` plus the connected pixels a little darker than the paper that
    touch it, grown a little; `core_mask` itself when that would more than
    double it."""
    import cv2
    kernel = np.ones((3, 3), np.uint8)
    _, halo = cv2.threshold(gray_roi, int(min(paper * _LETTERING_HALO_OF_PAPER, 245)), 255,
                            cv2.THRESH_BINARY_INV)
    halo = cv2.dilate(halo, kernel, iterations=_LETTERING_HALO_GROW_ITERATIONS)
    _, labels = cv2.connectedComponents((halo > 0).astype(np.uint8), connectivity=8)
    touched = np.unique(labels[core_mask > 0])
    grown = np.isin(labels, touched[touched > 0]) | (core_mask > 0)
    if grown.sum() > _LETTERING_HALO_MAX_GROWTH * max(int((core_mask > 0).sum()), 1):
        return core_mask
    return cv2.dilate(grown.astype(np.uint8) * 255, kernel, iterations=_LETTERING_FINAL_GROW_ITERATIONS)


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
_FRAME_MAX_FILL = 0.15


def _drop_hollow_frames(mask, gray_roi) -> None:
    """Clears, in place, the mask components that span much of the crop in both
    directions but hold little ink: a caption frame's border and ornaments, and
    the line art they touch. Lettering lines are small in one direction or
    dense. The ink is counted from the gray crop, not the mask, because the
    mask's halo grows a thin frame into something that looks solid. Erasing a
    frame left a pale patch with the lettering's ghost where it had been."""
    import cv2
    paper = float(np.median(gray_roi))
    ink = gray_roi < int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    rh, rw = mask.shape[:2]
    for i in range(1, count):
        x, y, w, h, _area = stats[i]
        if w >= _FRAME_MIN_SPAN * rw and h >= _FRAME_MIN_SPAN * rh:
            if (ink & (labels == i)).sum() <= _FRAME_MAX_FILL * w * h:
                mask[labels == i] = 0
                _restore_glyphs(mask, ink & (labels == i))


_FRAME_GLYPH_MAX_SPAN = 0.12
_FRAME_GLYPH_MIN_SPAN = 8
_FRAME_GLYPH_MIN_FILL = 0.25


def _restore_glyphs(mask, ink) -> None:
    """Puts back, in place, the small ink marks of a dropped frame component:
    lettering that touches the frame or a watermark shares its component, and
    leaving it out left the original letters showing under the English."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    limit = _FRAME_GLYPH_MAX_SPAN * min(mask.shape[:2])
    keep = np.zeros(mask.shape, np.uint8)
    for i in range(1, count):
        w, h, area = (stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
                      stats[i, cv2.CC_STAT_AREA])
        # A glyph is compact; the art's stray line fragments are thin or sparse.
        if _FRAME_GLYPH_MIN_SPAN <= min(w, h) and max(w, h) <= limit and area >= _FRAME_GLYPH_MIN_FILL * w * h:
            keep[labels == i] = 255
    mask[cv2.dilate(keep, np.ones((3, 3), np.uint8), iterations=3) > 0] = 255


_ART_FILL_CONTEXT_PX = 60
_ART_FILL_FEATHER_PX = 8
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


def _tone_from_clear_pixels(area, clear):
    """The art's tone at every pixel, averaged from the `clear` (1.0) pixels.
    Widens the blur where a lettered block is too large for the narrow one to
    reach clear pixels: dividing by a clamped, vanishing weight faded the fill to
    black in the middle of a big sign."""
    import cv2
    def scale(sigma):
        weight = cv2.GaussianBlur(clear, (0, 0), sigma)
        tone = cv2.GaussianBlur(area * clear[..., None], (0, 0), sigma) \
            / np.maximum(weight[..., None], 1e-6)
        return tone, np.minimum(weight * 20, 1.0)

    tone, reach = scale(15)
    for sigma in (45, 135):
        wider, wider_reach = scale(sigma)
        # Pixels with little narrow-scale support take the wider estimate.
        tone = tone * reach[..., None] + wider * (1 - reach[..., None])
        reach = np.maximum(reach, wider_reach)
    return tone


def _box_edge_ramp(shape, ox: int, oy: int, bw: int, bh: int, ih: int, iw: int, x: int, y: int):
    """Weight 0 on the box's own edge rising to 1 a few pixels in, so the fill
    meets the untouched art outside the box without a straight seam. Sides on
    the page border have nothing outside to meet and are left alone."""
    ramp = np.ones(shape, np.float32)
    steps = (np.arange(_ART_FILL_FEATHER_PX, dtype=np.float32) / _ART_FILL_FEATHER_PX)
    for side, on_page_edge in (("l", x == 0), ("r", x + bw >= iw), ("t", y == 0), ("b", y + bh >= ih)):
        if on_page_edge:
            continue
        for i, v in enumerate(steps):
            if side == "l":
                ramp[:, ox + i] = np.minimum(ramp[:, ox + i], v)
            elif side == "r":
                ramp[:, ox + bw - 1 - i] = np.minimum(ramp[:, ox + bw - 1 - i], v)
            elif side == "t":
                ramp[oy + i, :] = np.minimum(ramp[oy + i, :], v)
            else:
                ramp[oy + bh - 1 - i, :] = np.minimum(ramp[oy + bh - 1 - i, :], v)
    return ramp


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
    tone = _tone_from_clear_pixels(area, clear)
    weight = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 4)
    weight = (weight * _box_edge_ramp(area.shape[:2], x - x0, y - y0, bw, bh, ih, iw, x, y))[..., None]
    blended = (tone * weight + area * (1 - weight)).clip(0, 255).astype(np.uint8)
    return blended[y - y0:y - y0 + bh, x - x0:x - x0 + bw]


# Lettering is near-white and barely coloured; pale art such as a tan coat is
# brighter than the dark ground but fails one of these, so it stays unmasked.
_STROKE_MIN_GRAY = 200
_STROKE_MAX_SATURATION = 60


def _dark_art_stroke_mask(crop):
    """uint8 mask (255 = erase) of just the white letter strokes and their fill
    plus a thin margin for the glow, so LaMa repaints the lettering and leaves
    the art between and around the letters alone."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    core = ((gray >= _STROKE_MIN_GRAY) & (saturation <= _STROKE_MAX_SATURATION)).astype(np.uint8) * 255
    core = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=2)
    core = _fill_enclosed_holes(core)
    return cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))


def _dark_art_inpaint(img, x: int, y: int, bw: int, bh: int, backend: str, hf_token):
    """The crop for lettering on dark art. LaMa keeps the art's edges and
    texture where the flat tone fill smears them, so it goes first when the
    model is usable; the tone fill is the fallback and the answer without it."""
    import cv2
    crop = img[y:y + bh, x:x + bw]
    if _resolve_inpaint_backend(backend) != "ml" or not _pale_pixels(crop).any():
        return _dark_art_fill(img, x, y, bw, bh)
    mask = _dark_art_stroke_mask(crop)
    if not mask.any():
        return _dark_art_fill(img, x, y, bw, bh)
    lama, _ = _try_ml_inpaint(crop, mask, hf_token, backend)
    if lama is None:
        return _dark_art_fill(img, x, y, bw, bh)
    ih, iw = img.shape[:2]
    weight = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 4)
    weight *= _box_edge_ramp(weight.shape, 0, 0, bw, bh, ih, iw, x, y)
    blended = lama * weight[..., None] + crop * (1 - weight[..., None])
    return blended.clip(0, 255).astype(np.uint8)


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
    if _shaded_away_from_lettering(gray, mask, paper, median):
        return inpainted
    out = inpainted.copy()
    out[core] = np.median(roi[paper], axis=0).astype(inpainted.dtype)
    return out


_HALO_REACH_PX = 13


def _shaded_away_from_lettering(gray, mask, paper, median: float) -> bool:
    """True when the paper beyond the halo of the lettering has patches clearly
    darker than its median: a gradient, cloud or tone that flattening would turn
    into a white block. The halo itself is darker by design, so it is left out."""
    import cv2
    near = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_HALO_REACH_PX, _HALO_REACH_PX))).astype(bool)
    far = gray[paper & ~near]
    if far.size < 50:
        return False
    shaded = float((far < median - scanlate_tuning.INPAINT_FLAT_PAPER_SHADE_DEPTH).mean())
    return shaded > scanlate_tuning.INPAINT_FLAT_PAPER_MAX_SHADED


_AROUND_LETTERING_GROW_PX = 15
# Pixels this much darker than the paper or lighter are the halo; darker ones are art.
_AROUND_LETTERING_HALO_DEPTH = 40


_AROUND_LETTERING_MIN_INK_FILL = 0.15


def _keep_dense_marks(mask, gray):
    """`mask` without its sparse components: a line of lettering is dense with
    ink, a few strokes of the art round it are not, and whitening those would
    leave a pale patch in the picture."""
    import cv2
    ink = gray < int(min(max(150, float(np.median(gray)) * _LETTERING_CUTOFF_OF_PAPER), 240))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    out = np.zeros_like(mask)
    for i in range(1, count):
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if (ink & (labels == i)).sum() >= _AROUND_LETTERING_MIN_INK_FILL * w * h:
            out[labels == i] = mask[labels == i]
    return out


def _flatten_around_lettering(roi, inpainted, mask):
    """`inpainted` with the pale halo round masked lettering set to the paper
    colour, for lettering on flat paper that has no balloon outline to measure
    (a caption frame on a sign region). The mask misses the faint halo a JPEG
    leaves round each stroke, and it showed as a smudge of ghost text under the
    English. The lettering itself is set to the paper colour too, because the
    model repaints a flat row of lettering with a faint copy of it. Paper that
    varies, or too little of it to measure, is returned as it is, and pixels
    darker than the halo and outside the mask (the frame, the art) are kept."""
    import cv2
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = _keep_dense_marks(mask, gray)
    near = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_AROUND_LETTERING_GROW_PX, _AROUND_LETTERING_GROW_PX))).astype(bool)
    ring = near & (mask == 0)
    if int(ring.sum()) < 50:
        return inpainted
    median = float(np.median(gray[ring]))
    light = gray[ring][gray[ring] >= median]
    if float(light.std()) > scanlate_tuning.INPAINT_FLAT_PAPER_STD:
        return inpainted
    out = inpainted.copy()
    halo = near & ((gray >= median - _AROUND_LETTERING_HALO_DEPTH) | (mask > 0))
    out[halo] = np.median(roi[ring & (gray >= median - 5)], axis=0).astype(inpainted.dtype)
    return out


def _grown_erase_mask(mask, grow: float, keep):
    """`mask` widened by about 2px per 0.1 of `grow` above 1.0, kept inside `keep` (the
    balloon's outline) when there is one."""
    import cv2
    if grow <= 1.0 or not mask.any():
        return mask
    reach = max(1, round((grow - 1.0) * 20))
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1)))
    if keep is not None:
        mask[~keep] = 0
    return mask


# Lettering is the darkest ink in a balloon: a pixel counts as lettering when it is within this
# share of the way from the darkest strokes to the paper. Hatching and tone drawn in a lighter
# line fall outside it.
_HATCHING_STROKE_DEPTH = 0.35
# A mark this far from any stroke is not an edge or halo of it.
_HATCHING_REACH_PX = 6

def _without_hatching(gray_roi, keep):
    """`gray_roi` with the pale marks that sit away from the lettering set to the paper's gray,
    so the erase mask follows the lettering and leaves the hatching or screentone drawn inside
    a balloon alone (erasing it left a hard-edged white patch). Unchanged unless such marks
    outweigh the lettering, so an ordinary balloon, or one with a little furigana, is
    erased exactly as before. `keep`: where the balloon's interior is."""
    import cv2
    if background_is_dark(gray_roi):
        return gray_roi
    paper = float(np.median(gray_roi))
    cutoff = int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
    ink = gray_roi < cutoff
    # The outline, and any hatching joined to it, is darker than the lettering can be, so the
    # darkest strokes are read from the marks that are not part of such a frame.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    rh, rw = gray_roi.shape
    lettering = ink.copy()
    for i in range(1, count):
        w, h, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        if w >= _FRAME_MIN_SPAN * rw and h >= _FRAME_MIN_SPAN * rh and area <= _FRAME_MAX_FILL * w * h:
            lettering[labels == i] = False
    sample = gray_roi[lettering & keep]
    if sample.size < 30:
        return gray_roi
    darkest = float(np.percentile(sample, 10))
    strokes = ink & (gray_roi <= darkest + _HATCHING_STROKE_DEPTH * (paper - darkest))
    reach = 2 * _HATCHING_REACH_PX + 1
    near = cv2.dilate(strokes.astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))).astype(bool)
    if int((ink & ~near).sum()) <= int(strokes.sum()):
        return gray_roi
    cleared = gray_roi.copy()
    cleared[(gray_roi < int(min(paper * _LETTERING_HALO_OF_PAPER, 245))) & ~near] = int(paper)
    return cleared


def _inside_in_crop(inside, box: dict, x: int, y: int, bw: int, bh: int):
    """The balloon's `inside` array (laid over `box`) as a bool array over the padded crop
    that starts at (x, y), False outside the box."""
    keep = np.zeros((bh, bw), dtype=bool)
    oy, ox = box["y"] - y, box["x"] - x
    part = inside[max(0, -oy):, max(0, -ox):]
    part = part[:bh - max(oy, 0), :bw - max(ox, 0)]
    keep[max(oy, 0):max(oy, 0) + part.shape[0], max(ox, 0):max(ox, 0) + part.shape[1]] = part
    return keep


def _lettering_glyphs(gray_roi):
    """Label image, stats and centres of the dark marks of `gray_roi`, and the ids of those
    that look like a glyph: of letter size and sitting on pale paper (a ring round them
    that is mostly light), as lettering on a white band does and art's own line work
    mostly does not."""
    import cv2
    h, w = gray_roi.shape
    count, labels, stats, centres = cv2.connectedComponentsWithStats(
        (gray_roi < scanlate_tuning.LINE_INK_LEVEL).astype(np.uint8), connectivity=8)
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    glyphs = []
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        if area < scanlate_tuning.LINE_GLYPH_MIN_AREA or not (
                scanlate_tuning.LINE_GLYPH_MIN_SPAN <= max(cw, ch) <= scanlate_tuning.LINE_GLYPH_MAX_SPAN * h):
            continue
        x0, y0, x1, y1 = max(0, x - 5), max(0, y - 5), min(w, x + cw + 5), min(h, y + ch + 5)
        mark = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = (cv2.dilate(mark, ring_kernel) > 0) & (mark == 0)
        if (gray_roi[y0:y1, x0:x1][ring] >= scanlate_tuning.LINE_PAPER_LEVEL).mean() >= scanlate_tuning.LINE_RING_PALE_SHARE:
            glyphs.append(i)
    return labels, stats, centres, glyphs


def _strokes_on_a_line(gray_roi):
    """(mask, slant in degrees, typical glyph size) of one line of dark lettering set on a pale
    band over busy art, or None when the crop holds no such line; the mask is uint8, 255 =
    erase. The paper-relative mask reads the dark
    art round such a band as lettering too, covers nearly the whole crop, and the
    repaint whitened the art out. The letters are found as glyph-sized dark marks on pale
    paper that lie along one straight (possibly tilted) line; marks off that line (windows,
    hatching) are left alone."""
    import cv2
    labels, stats, centres, glyphs = _lettering_glyphs(gray_roi)
    anchors = [i for i in glyphs if stats[i, cv2.CC_STAT_AREA] >= scanlate_tuning.LINE_ANCHOR_AREA
               and min(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= scanlate_tuning.LINE_ANCHOR_THICKNESS]
    if len(anchors) < scanlate_tuning.LINE_MIN_GLYPHS:
        return None
    typical = float(np.median([max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) for i in anchors]))
    # Refitted on the marks that lie along the first fit, so a few stray marks (hatching, windows)
    # cannot hold the angle off the real line.
    for _ in range(2):
        points = np.array([centres[i] for i in anchors], np.float32)
        vx, vy, px, py = cv2.fitLine(points, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        near = [i for i in anchors if abs((centres[i][0] - px) * vy - (centres[i][1] - py) * vx)
                <= scanlate_tuning.LINE_REACH * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], typical)]
        if len(near) < scanlate_tuning.LINE_MIN_GLYPHS:
            return None
        anchors = near
    points = np.array([centres[i] for i in anchors], np.float32)
    along = (points[:, 0] - px) * vx + (points[:, 1] - py) * vy
    if float(along.max() - along.min()) < scanlate_tuning.LINE_MIN_LENGTH_IN_GLYPHS * typical:
        return None
    mask = np.zeros(gray_roi.shape, np.uint8)
    for i in glyphs:
        reach = scanlate_tuning.LINE_REACH * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], typical)
        if abs((centres[i][0] - px) * vy - (centres[i][1] - py) * vx) <= reach:
            mask[labels == i] = 255
    grown = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return grown, math.degrees(math.atan2(vy, vx)) * (1 if vx >= 0 else -1), typical


def sign_text_line(page_bgr, box: dict):
    """(corridor, angle, size) of the single line of lettering on a pale band over busy art, or
    None. `corridor` is a bool array the size of `box`, True along the line's own strip, `angle`
    its slant in degrees (positive when it runs down to the right) and `size` the font size that
    fits the strip's height, so the English can be set along the band instead of in the level
    box that spills over the art. Read from the page
    before the erase, and only where the erase itself takes the line-only path."""
    import cv2
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    gray = cv2.cvtColor(page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])], cv2.COLOR_BGR2GRAY)
    if gray.size == 0 or (_lettering_mask(gray) > 0).mean() <= scanlate_tuning.LINE_ONLY_MASK_SHARE:
        return None
    found = _strokes_on_a_line(gray)
    if found is None:
        return None
    strokes, angle, typical = found
    reach = max(3, round(scanlate_tuning.LINE_CORRIDOR_CLOSE * typical)) | 1
    corridor = cv2.morphologyEx(strokes, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    margin = max(3, round(scanlate_tuning.LINE_CORRIDOR_MARGIN * typical)) | 1
    corridor = cv2.dilate(corridor, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (margin, margin)))
    return corridor > 0, angle, round(scanlate_tuning.LINE_TEXT_SIZE_OF_GLYPH * typical)


def inpaint_region(image_path: str, box: dict, out_path: str | None = None, padding: int = 4,
                    backend: str = "auto", hf_token: str | None = None, image=None, inside=None,
                    spare_art: bool = False, on_art: bool = False, grow: float = 1.0):
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
    signs opt in.

    grow: above 1.0 widens the erased area around the lettering (a stronger erase for ghosts
    that survive the default), by about 2px per 0.1 step, still only inside `inside`."""
    import cv2
    img = image if image is not None else cv2.imread(image_path)
    h, w = img.shape[:2]
    x = max(0, box["x"] - padding)
    y = max(0, box["y"] - padding)
    bw = min(w - x, box["w"] + 2 * padding)
    bh = min(h - y, box["h"] + 2 * padding)

    roi = img[y:y + bh, x:x + bw]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    if inside is not None:
        keep = _inside_in_crop(inside, box, x, y, bw, bh)
        mask = _lettering_mask(_without_hatching(gray_roi, keep))
        mask[~keep] = 0
    else:
        mask = _lettering_mask(gray_roi)
    line = None
    if inside is None and on_art and (mask > 0).mean() > scanlate_tuning.LINE_ONLY_MASK_SHARE:
        line = _strokes_on_a_line(gray_roi)
    line_only = line is not None
    if line_only:
        mask = line[0]
    elif inside is None and spare_art:
        _drop_marks_running_off_edge(mask)
    elif inside is None and on_art:
        _drop_hollow_frames(mask, gray_roi)
    mask = _grown_erase_mask(mask, grow, keep if inside is not None else None)

    inpainted_roi = None
    if inside is None and on_art and is_coloured_art(roi) and _pale_pixels(roi).any():
        # An outline-only mask left the letters' fill and glow behind as ghosts.
        mask = _dark_art_lettering_mask(roi)
    elif inside is None and (spare_art or on_art) and background_is_dark(gray_roi):
        inpainted_roi = _dark_art_inpaint(img, x, y, bw, bh, backend, hf_token)
    fallback_error = None
    resolved = "cv" if inpainted_roi is not None else _resolve_inpaint_backend(backend)
    if resolved == "ml" and inpainted_roi is None:
        inpainted_roi, fallback_error = _try_ml_inpaint(roi, mask, hf_token, backend)

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    if inside is not None and not background_is_dark(gray_roi):
        inpainted_roi = _flatten_plain_paper(roi, inpainted_roi, mask, keep)
    elif inside is None and on_art and not line_only and not background_is_dark(gray_roi) and mask.any():
        inpainted_roi = _flatten_around_lettering(roi, inpainted_roi, mask)
    img[y:y + bh, x:x + bw] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
    result = out_path or img

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
    result = out_path or img

    if fallback_error is not None:
        fallback_error.fell_back_to_cv = True
        fallback_error.result = result
        raise fallback_error
    return result
