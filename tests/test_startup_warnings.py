"""Startup dependency warnings: fakes only, no real torch/GPU/deno."""
import datetime
import types

import pytest

import diagnostics
import diagnostics_torch


def _versions(monkeypatch, **installed):
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        lambda name: installed.get(name))


def test_ytdlp_old_and_fresh(monkeypatch):
    _versions(monkeypatch, **{"yt-dlp": "2026.1.1"})
    assert diagnostics._warn_ytdlp_old(datetime.date(2026, 4, 2))  # 91 days
    assert diagnostics._warn_ytdlp_old(datetime.date(2026, 3, 31)) is None
    _versions(monkeypatch, **{"yt-dlp": "2026.01.01.123456"})  # nightly suffix
    assert diagnostics._warn_ytdlp_old(datetime.date(2026, 6, 1))
    _versions(monkeypatch)
    assert diagnostics._warn_ytdlp_old() is None


@pytest.mark.parametrize("out,warn", [("deno 2.1.4 (stable)\nv8 13", True),
                                      ("deno 2.3.0 (stable)", False),
                                      ("deno 10.0.0", False), ("garbage", False)])
def test_deno_version(monkeypatch, out, warn):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: "/x/deno")
    monkeypatch.setattr(diagnostics.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=out))
    assert bool(diagnostics._warn_deno_old()) is warn


def test_deno_absent(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: None)
    assert diagnostics._warn_deno_old() is None


def test_qwen_with_transformers_5(monkeypatch):
    _versions(monkeypatch, **{"qwen-asr": "0.1", "transformers": "5.6.0"})
    assert diagnostics._warn_qwen_transformers()
    _versions(monkeypatch, **{"qwen-asr": "0.1", "transformers": "4.57.6"})
    assert diagnostics._warn_qwen_transformers() is None
    _versions(monkeypatch, **{"transformers": "5.6.0"})
    assert diagnostics._warn_qwen_transformers() is None


@pytest.mark.parametrize("system,path,qwen,warn", [
    ("Windows", "C:\\Users\\用户\\Baihe", True, True),
    ("Windows", "C:\\Users\\bob\\Baihe", True, False),
    ("Windows", "C:\\Users\\用户\\Baihe", False, False),
    ("Linux", "/home/用户", True, False)])
def test_qwen_nonascii_path(monkeypatch, system, path, qwen, warn):
    import portable
    monkeypatch.setattr(diagnostics.platform, "system", lambda: system)
    monkeypatch.setattr(portable, "data_dir", lambda: path)
    _versions(monkeypatch, **({"qwen-asr": "0.1"} if qwen else {}))
    msg = diagnostics._warn_qwen_nonascii_path()
    assert bool(msg) is warn
    if msg:
        assert "Baihe" not in msg and "用户" not in msg and "\\" not in msg


@pytest.mark.parametrize("pyannote,gpu,warn", [
    ("4.0.1", {"available": True, "vram_total_gb": 8.0}, True),
    ("4.0.1", {"available": True, "vram_total_gb": 12.0}, False),
    ("4.0.1", {"available": True, "vram_total_gb": 11.76}, False),   # a 12 GB RTX 3080 Ti as PyTorch reports it
    ("4.0.1", {"available": True, "vram_total_gb": 10.7}, True),    # an 11 GB card
    ("4.0.1", {"available": False, "message": "no gpu"}, False),
    ("3.3.2", {"available": True, "vram_total_gb": 8.0}, False),
    (None, {"available": True, "vram_total_gb": 8.0}, False)])
def test_low_vram_pyannote(monkeypatch, pyannote, gpu, warn):
    _versions(monkeypatch, **({"pyannote.audio": pyannote} if pyannote else {}))
    monkeypatch.setattr(diagnostics_torch, "get_gpu_status", lambda: gpu)
    assert bool(diagnostics._warn_low_vram_pyannote()) is warn


def test_startup_warnings_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("C:\\secret\\path")
    monkeypatch.setattr(diagnostics, "get_installed_version", boom)
    monkeypatch.setattr(diagnostics.shutil, "which", boom)
    monkeypatch.setattr(diagnostics_torch, "get_gpu_status", boom)
    assert diagnostics.startup_warnings() == []


def test_startup_warnings_collects(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: None)
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    _versions(monkeypatch, **{"qwen-asr": "0.1", "transformers": "5.0.0"})
    assert len(diagnostics.startup_warnings()) == 1
