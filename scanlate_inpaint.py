"""
scanlate_inpaint.py -- erase the original text inside a bubble or mask
(LaMa-manga model, or plain OpenCV inpainting as the fallback).
"""

import os

import numpy as np


_LAMA_ML_REPO = "mayocream/lama-manga"


def lama_ml_weights_cached() -> bool:
    """Same idea as bubble_ml_weights_cached(), for the LaMa-manga
    inpainting checkpoint."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    hit = try_to_load_from_cache(repo_id=_LAMA_ML_REPO, filename="model.safetensors")
    return isinstance(hit, str) and os.path.exists(hit)


class InpaintModelUnavailable(RuntimeError):
    """The ML inpainting backend couldn't be loaded or run. Carries
    whether plain OpenCV inpainting already ran as a fallback, matching
    BubbleModelUnavailable's shape so callers can handle both the same
    way."""

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv


def _build_lama_generator():
    """
    Constructs the FFC-ResNet generator architecture LaMa's published
    checkpoints use -- unchanged from the original saic-mdal/lama
    paper's default config (ngf=64, 3 downsampling stages, 9 FFC
    residual blocks, global-feature ratio 0.75), which is what every
    public LaMa fine-tune this project found (including manga/anime
    ones) keeps unchanged, only retraining weights.

    NOTE, same honesty as detect_bubbles_ml()'s own docstring: written
    against the published architecture, not verified against
    mayocream/lama-manga's actual state_dict key names.
    _load_lama_generator() loads with strict=False and refuses to use
    the result if most of the checkpoint's weights don't match this
    shape, so a naming mismatch fails loudly and falls back to plain
    OpenCV inpainting rather than silently running with near-random
    weights. If that happens, inspect the real checkpoint's state_dict
    keys (`safetensors.torch.load_file(path).keys()`) and adjust the
    module names below to match.

    Lazily imports torch so nothing else in this file -- including
    detect_bubbles_cv()/inpaint_region()'s OpenCV-only default path --
    ever needs it installed at all.
    """
    import torch
    import torch.nn as nn

    class FourierUnit(nn.Module):
        def __init__(self, channels):
            super().__init__()
            self.conv = nn.Conv2d(channels * 2, channels * 2, kernel_size=1, bias=False)
            self.bn = nn.BatchNorm2d(channels * 2)
            self.relu = nn.ReLU(inplace=True)

        def forward(self, x):
            b, c, h, w = x.shape
            ffted = torch.fft.rfft2(x, norm="ortho")
            ffted = torch.stack([ffted.real, ffted.imag], dim=-1)
            ffted = ffted.permute(0, 1, 4, 2, 3).reshape(b, c * 2, *ffted.shape[2:4])
            ffted = self.relu(self.bn(self.conv(ffted)))
            ffted = ffted.reshape(b, c, 2, *ffted.shape[2:]).permute(0, 1, 3, 4, 2)
            ffted = torch.complex(ffted[..., 0].contiguous(), ffted[..., 1].contiguous())
            return torch.fft.irfft2(ffted, s=(h, w), norm="ortho")

    class SpectralTransform(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.conv1 = nn.Sequential(
                nn.Conv2d(in_ch, out_ch // 2, kernel_size=1, bias=False),
                nn.BatchNorm2d(out_ch // 2), nn.ReLU(inplace=True))
            self.fu = FourierUnit(out_ch // 2)
            self.conv2 = nn.Conv2d(out_ch // 2, out_ch, kernel_size=1, bias=False)

        def forward(self, x):
            x = self.conv1(x)
            return self.conv2(x + self.fu(x))

    class FFC(nn.Module):
        def __init__(self, in_ch, out_ch, ratio_gin, ratio_gout, kernel_size=3, padding=1):
            super().__init__()
            in_cg, in_cl = int(in_ch * ratio_gin), in_ch - int(in_ch * ratio_gin)
            out_cg, out_cl = int(out_ch * ratio_gout), out_ch - int(out_ch * ratio_gout)
            self.out_cl, self.out_cg = out_cl, out_cg
            conv = (lambda ci, co: nn.Conv2d(ci, co, kernel_size, padding=padding, bias=False)
                    if ci and co else None)
            self.convl2l = conv(in_cl, out_cl)
            self.convl2g = conv(in_cl, out_cg)
            self.convg2l = conv(in_cg, out_cl)
            self.convg2g = SpectralTransform(in_cg, out_cg) if in_cg and out_cg else None

        def forward(self, x_l, x_g):
            out_l = 0
            if self.out_cl:
                out_l = (self.convl2l(x_l) if self.convl2l else 0) + \
                        (self.convg2l(x_g) if self.convg2l else 0)
            out_g = 0
            if self.out_cg:
                out_g = (self.convl2g(x_l) if self.convl2g else 0) + \
                        (self.convg2g(x_g) if self.convg2g else 0)
            return out_l, out_g

    class FFCBlock(nn.Module):
        def __init__(self, channels, ratio=0.75):
            super().__init__()
            local_ch, global_ch = channels - int(channels * ratio), int(channels * ratio)
            self.ffc1 = FFC(channels, channels, ratio, ratio)
            self.bn_l1, self.bn_g1 = nn.BatchNorm2d(local_ch), nn.BatchNorm2d(global_ch)
            self.ffc2 = FFC(channels, channels, ratio, ratio)
            self.bn_l2, self.bn_g2 = nn.BatchNorm2d(local_ch), nn.BatchNorm2d(global_ch)
            self.act = nn.ReLU(inplace=True)

        def forward(self, x_l, x_g):
            id_l, id_g = x_l, x_g
            l, g = self.ffc1(x_l, x_g)
            l, g = self.act(self.bn_l1(l)), self.act(self.bn_g1(g))
            l, g = self.ffc2(l, g)
            l, g = self.act(self.bn_l2(l)), self.act(self.bn_g2(g))
            return id_l + l, id_g + g

    class Generator(nn.Module):
        def __init__(self, ngf=64, n_down=3, n_blocks=9, ratio=0.75):
            super().__init__()
            self.stem = nn.Sequential(
                nn.ReflectionPad2d(3), nn.Conv2d(4, ngf, 7, bias=False),
                nn.BatchNorm2d(ngf), nn.ReLU(inplace=True))
            down, ch = [], ngf
            for _ in range(n_down):
                down += [nn.Conv2d(ch, ch * 2, 3, stride=2, padding=1, bias=False),
                          nn.BatchNorm2d(ch * 2), nn.ReLU(inplace=True)]
                ch *= 2
            self.down = nn.Sequential(*down)
            self.blocks = nn.ModuleList([FFCBlock(ch, ratio) for _ in range(n_blocks)])
            up = []
            for _ in range(n_down):
                up += [nn.ConvTranspose2d(ch, ch // 2, 3, stride=2, padding=1, output_padding=1),
                       nn.BatchNorm2d(ch // 2), nn.ReLU(inplace=True)]
                ch //= 2
            self.up = nn.Sequential(*up)
            self.head = nn.Sequential(nn.ReflectionPad2d(3), nn.Conv2d(ch, 3, 7), nn.Sigmoid())
            self._ratio = ratio

        def forward(self, x):
            x = self.down(self.stem(x))
            split = x.shape[1] - int(x.shape[1] * self._ratio)
            x_l, x_g = x[:, :split], x[:, split:]
            for block in self.blocks:
                x_l, x_g = block(x_l, x_g)
            return self.head(self.up(torch.cat([x_l, x_g], dim=1)))

    return Generator()


def _load_lama_generator(hf_token: str = None):
    """Downloads (or reuses the already-cached) LaMa-manga checkpoint and
    loads it into _build_lama_generator()'s architecture. Raises
    RuntimeError -- caught by inpaint_region()/inpaint_mask_region() and
    turned into InpaintModelUnavailable -- if most of the checkpoint's
    weights don't match, rather than silently returning a near-random
    model."""
    global _lama_model
    if "_lama_model" in globals():
        return globals()["_lama_model"]

    import os as _os
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file as _load_safetensors

    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        _os.environ.setdefault("HF_TOKEN", _tok)

    ckpt_path = hf_hub_download(repo_id=_LAMA_ML_REPO, filename="model.safetensors", token=_tok)
    state_dict = _load_safetensors(ckpt_path)

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
    generator.eval()
    globals()["_lama_model"] = generator
    return generator


def _run_ml_inpaint(roi_bgr, mask_u8, hf_token: str = None):
    """Runs LaMa-manga inpainting on one region-of-interest. roi_bgr: an
    OpenCV BGR array. mask_u8: a same-size uint8 array, nonzero = erase
    this pixel. Returns a BGR array the same size as roi_bgr, with only
    the masked pixels replaced -- everywhere else stays byte-identical
    to the input, same "only touch what was actually erased" discipline
    as the OpenCV path below."""
    import numpy as np
    import torch
    import cv2

    generator = _load_lama_generator(hf_token=hf_token)
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


def _resolve_inpaint_backend(backend: str) -> str:
    if backend == "auto":
        return "ml" if lama_ml_weights_cached() else "cv"
    return backend


def inpaint_region(image_path: str, box: dict, out_path: str = None, padding: int = 4,
                    backend: str = "auto", hf_token: str = None):
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
    detect_bubbles()."""
    import cv2
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    x = max(0, box["x"] - padding)
    y = max(0, box["y"] - padding)
    bw = min(w - x, box["w"] + 2 * padding)
    bh = min(h - y, box["h"] + 2 * padding)

    roi = img[y:y + bh, x:x + bw]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Text is the dark pixels inside an otherwise light bubble
    _, mask = cv2.threshold(gray_roi, 150, 255, cv2.THRESH_BINARY_INV)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)

    resolved = _resolve_inpaint_backend(backend)
    inpainted_roi = None
    fallback_error = None
    if resolved == "ml":
        try:
            inpainted_roi = _run_ml_inpaint(roi, mask, hf_token=hf_token)
        except ImportError as exc:
            fallback_error = InpaintModelUnavailable(
                "ML inpainting needs extra packages:\n"
                "    pip install torch safetensors huggingface_hub\n\n"
                "Falling back to plain OpenCV inpainting for this bubble."
            )
            fallback_error.__cause__ = exc
        except Exception as exc:
            fallback_error = InpaintModelUnavailable(
                f"LaMa-manga inpainting failed: {type(exc).__name__}: {exc}\n\n"
                "Falling back to plain OpenCV inpainting for this bubble."
            )
            fallback_error.__cause__ = exc

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
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


def inpaint_mask_region(image_path: str, mask, out_path: str = None, padding: int = 4,
                         backend: str = "auto", hf_token: str = None):
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
            inpainted_roi = _run_ml_inpaint(roi, roi_mask, hf_token=hf_token)
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
