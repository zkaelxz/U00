"""LaMa-manga inpainting model: weights cache check, loading, GPU placement and
running it on one region (scanlate_inpaint.py decides what to erase)."""

import os
import threading
import time

import model_pins
from lib import device_use

_LAMA_ML_REPO = "mayocream/lama-manga"
_LAMA_ML_FILE = "lama-manga.safetensors"
_LAMA_BLOCKS = 18

# The generator keeps 64-channel float32 activations at full resolution, so a
# region of a few megapixels (a user-drawn box, a tall webtoon strip) needs
# gigabytes. Past this size the OpenCV fallback is used instead.
_ML_MAX_PIXELS = 3_000_000

# The model and its GPU copy are process globals shared by Scanlate's render (under the pipeline
# lock) and the extension's overlay redraw (which is not), so a first load or GPU copy is never
# made twice and one region runs at a time.
_LAMA_LOCK = threading.Lock()

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
# Every driver or CUDA fault seen (not a full GPU), kept as a history.
_lama_gpu_broken: list = []
# A fault sends regions to the CPU for this long only; one fault (a driver hiccup) otherwise
# cost every later region the GPU until the app restarted. Same period as the text reader's.
_GPU_OFF_SECONDS = 300
_lama_gpu_off_until = 0.0
# On a 3080 Ti a page's balloons took about 1.2 s on the CPU. The GPU needs the weights
# (about 200 MB) and CUDA's own share once, plus working memory for the full-resolution
# 64-channel maps, which grows with the region; these are deliberately generous so a region
# only goes to the GPU when it clearly fits beside whatever else is loaded there.
_GPU_FIRST_LOAD_MB = 800
_GPU_MB_PER_MEGAPIXEL = 3000


def _lama_on_gpu(generator, pixels: int, torch):
    """The generator's GPU copy when a region of `pixels` fits on a free enough GPU and
    Settings allows GPU use, else None (the region runs on the CPU)."""
    if time.monotonic() < _lama_gpu_off_until or not torch.cuda.is_available():
        return None
    import vram
    import stored_settings
    if not stored_settings.read("use_gpu"):
        return None
    need = (0 if "cuda" in _lama_gpu else _GPU_FIRST_LOAD_MB) + pixels / 1_000_000 * _GPU_MB_PER_MEGAPIXEL
    try:
        # Also honours Settings > Keep free memory for the GPU.
        vram.check_fits("LaMa-manga", required_mb=need)
    except Exception:
        return None
    if "cuda" not in _lama_gpu:
        import copy
        _lama_gpu["cuda"] = copy.deepcopy(generator).to("cuda")
    return _lama_gpu["cuda"]


def _generate(generator, tensor, torch):
    """The generator's output for `tensor`, on the GPU when it fits there (see _lama_on_gpu),
    as a CPU tensor either way."""
    import vram
    with torch.no_grad(), vram.comic_gpu_use():
        try:
            gpu = _lama_on_gpu(generator, tensor.shape[-2] * tensor.shape[-1], torch)
            if gpu is not None:
                device_use.note("eraser (LaMa)", "GPU")
                out = gpu(tensor.to("cuda"))[0].cpu()
                if tensor.shape[-2] * tensor.shape[-1] > 1_000_000:
                    # A big region leaves gigabytes in torch's cache, which the text reader and
                    # the detector would otherwise be refused for until the idle release.
                    torch.cuda.empty_cache()
                return out
        except Exception as exc:
            # A GPU that filled up meanwhile costs only this region; any other fault (a
            # broken driver, a failed copy) would repeat on every region, so the CPU is kept for a while.
            if not isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())):
                global _lama_gpu_off_until
                _lama_gpu_broken.append(type(exc).__name__)
                _lama_gpu_off_until = time.monotonic() + _GPU_OFF_SECONDS
                _lama_gpu.clear()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        device_use.note("eraser (LaMa)", "CPU")
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

    with _LAMA_LOCK:
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

    with _LAMA_LOCK:
        out = _generate(generator, tensor, torch)
    out = out[:, :h, :w].clamp(0, 1).permute(1, 2, 0).numpy()
    out_bgr = cv2.cvtColor((out * 255).astype("uint8"), cv2.COLOR_RGB2BGR)

    mask3 = np.repeat((mask_u8 > 0)[..., None], 3, axis=2)
    return np.where(mask3, out_bgr, roi_bgr)
