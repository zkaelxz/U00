"""The comic detector/eraser GPU copies are freed after an idle spell, never mid-call."""

import sys
import time
import types

import pytest

import vram


@pytest.fixture
def fake_comic_modules(monkeypatch):
    lama = types.SimpleNamespace(_lama_gpu={"cuda": object()})
    detect = types.SimpleNamespace(_bubble_ml_gpu={"cuda": object()})
    monkeypatch.setitem(sys.modules, "scanlate.scanlate_lama", lama)
    monkeypatch.setitem(sys.modules, "scanlate.scanlate_detect", detect)
    monkeypatch.setattr(vram, "COMIC_GPU_IDLE_SECONDS", 0.2)
    yield lama, detect
    timer = vram._comic_timer
    if timer is not None:
        timer.cancel()
    vram._comic_timer = None


def _wait_until(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_copies_are_freed_after_the_idle_spell(fake_comic_modules):
    lama, detect = fake_comic_modules
    with vram.comic_gpu_use():
        pass
    assert _wait_until(lambda: not lama._lama_gpu and not detect._bubble_ml_gpu)


def test_a_call_in_progress_keeps_its_copies(fake_comic_modules):
    lama, detect = fake_comic_modules
    with vram.comic_gpu_use():
        time.sleep(0.6)   # well past the idle spell
        assert lama._lama_gpu and detect._bubble_ml_gpu
    assert _wait_until(lambda: not lama._lama_gpu)


def test_a_new_call_pushes_the_release_back(fake_comic_modules):
    lama, _detect = fake_comic_modules
    with vram.comic_gpu_use():
        pass
    time.sleep(0.12)
    with vram.comic_gpu_use():
        pass
    time.sleep(0.12)
    assert lama._lama_gpu   # 0.24 s since the first call, but only 0.12 s since the last
    assert _wait_until(lambda: not lama._lama_gpu)
