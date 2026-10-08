"""Step 95: background-music-preserving dub. Fully mocked: no real
separation, TTS or ffmpeg; numpy/soundfile tests are importorskip'd."""
import os
import queue

import pytest

import audio_preprocess
import background_jobs
import dub
from core import Line
from services import dub_service, settings_service
from services.service_errors import DependencyUnavailableError, InvalidInputError


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)
    monkeypatch.setattr(dub_service, "_missing_engine_dependency", lambda e: None)
    monkeypatch.setattr(dub_service, "_missing_separation_dependency", lambda b: None)
    monkeypatch.setattr(background_jobs, "get_status", lambda j: None)

    def fake_start(job_id, target, args=(), gpu_touching=False, description=None, on_done=None):
        calls.append(dict(target=target, args=args))
        return True
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
    return calls


def _seed(db, audio=True, **fields):
    did = db.create_drama(title_en="D", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hi", speaker="S1")])
    if audio:
        with open(os.path.join(db.drama_dir(did), "a.wav"), "wb") as f:
            f.write(b"x")
        db.update_drama(did, audio_filename="a.wav")
    return did


def test_keep_background_binds_source_and_backend(isolated_db, started):
    did = _seed(isolated_db, separation_backend="demucs")
    dub_service.start_dub_run(did, keep_background=True)
    target = started[0]["target"]
    assert target.keywords["background_source"].endswith("a.wav")
    assert target.keywords["separation_backend"] == "demucs"
    assert len(started[0]["args"]) == 6  # queue is appended by background_jobs


def test_default_has_no_background(isolated_db, started):
    did = _seed(isolated_db)
    dub_service.start_dub_run(did)
    assert started[0]["target"].keywords["background_source"] is None


def test_keep_background_needs_audio_and_video_dub(isolated_db, started):
    with pytest.raises(InvalidInputError):
        dub_service.start_dub_run(_seed(isolated_db, audio=False), keep_background=True)
    with pytest.raises(InvalidInputError):
        dub_service.start_dub_run(_seed(isolated_db, content_mode="novel_narration"),
                                  keep_background=True)


def test_missing_separation_dependency_is_503_fixed_text(isolated_db, started, monkeypatch):
    monkeypatch.setattr(dub_service, "_missing_separation_dependency", lambda b: "fixed text")
    with pytest.raises(DependencyUnavailableError) as ei:
        dub_service.start_dub_run(_seed(isolated_db), keep_background=True)
    assert "fixed text" in str(ei.value)


def test_config_flag_is_boolean(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)
    monkeypatch.setattr(dub_service, "_missing_separation_dependency", lambda b: None)
    did = _seed(isolated_db)
    assert dub_service.get_dub_config(did)["can_keep_background"] is True
    assert dub_service.get_dub_config(_seed(isolated_db, audio=False))["can_keep_background"] is False


def _worker(monkeypatch, tmp_path, mixer):
    monkeypatch.setattr(dub, "build_dub_track",
                        lambda *a, **k: (str(tmp_path / "dub_track.wav"), []))
    monkeypatch.setattr(dub, "mix_original_background", mixer)
    q = queue.Queue()
    dub.build_track_subprocess_worker(
        [Line(idx=0, start=0, end=1, zh="x", en="h")], str(tmp_path), {}, False, 1.4, 0.85, q,
        background_source="/src/a.wav")
    return q.get_nowait()


def test_worker_mixes_background(monkeypatch, tmp_path):
    seen = {}
    outcome = _worker(monkeypatch, tmp_path, lambda *a, **k: seen.update(a=a, k=k))
    assert outcome[0] == "ok" and outcome[1]["background_mixed"] is True
    assert seen["a"][1] == "/src/a.wav"


def test_worker_keeps_plain_track_when_separation_fails(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise audio_preprocess.VocalSeparationError("secret /path detail")
    outcome = _worker(monkeypatch, tmp_path, boom)
    assert outcome[0] == "ok" and outcome[1]["background_mixed"] is False
    assert "/path" not in outcome[1]["background_error"]


def test_extract_background_subtracts_vocals(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    src, out = str(tmp_path / "src.wav"), str(tmp_path / "bg.wav")
    mix = np.full((100, 1), 0.5, dtype="float32")
    sf.write(src, mix, 8000)

    def fake_sep(audio_path, out_path, backend="auto", progress_cb=None, cancel_check_cb=None):
        sf.write(out_path, np.full((50, 1), 0.2, dtype="float32"), 4000)  # half rate
        return out_path
    monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_sep)
    audio_preprocess.extract_background(src, out)
    bg, sr = sf.read(out, always_2d=True)
    assert sr == 8000 and len(bg) == 100
    assert bg[10, 0] == pytest.approx(0.3, abs=1e-3)


# ---- B-23: background cache keyed on backend + source size/mtime ----------

@pytest.fixture
def fake_mix(monkeypatch):
    """No pydub or real separation: counts extract_background calls."""
    import sys
    import types

    class Seg:
        @classmethod
        def from_file(cls, path):
            return cls()

        def __add__(self, gain):
            return self

        def overlay(self, other):
            return self

        def export(self, path, format=None):
            pass
    monkeypatch.setitem(sys.modules, "pydub", types.SimpleNamespace(AudioSegment=Seg))
    calls = []

    def fake_extract(src, out, backend="auto", **kw):
        calls.append(backend)
        with open(out, "wb") as f:
            f.write(b"bg")
        return out
    monkeypatch.setattr(audio_preprocess, "extract_background", fake_extract)
    return calls


def _source(tmp_path, data=b"source"):
    src = tmp_path / "a.wav"
    src.write_bytes(data)
    return str(src)


def test_background_cache_reused_when_nothing_changed(tmp_path, fake_mix):
    src = _source(tmp_path)
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    assert fake_mix == ["demucs"]
    assert (tmp_path / dub.BACKGROUND_META_FILENAME).exists()


def test_background_reseparated_when_backend_changes(tmp_path, fake_mix):
    src = _source(tmp_path)
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="uvr")
    assert fake_mix == ["demucs", "uvr"]


def test_background_reseparated_when_source_replaced_with_older_mtime(tmp_path, fake_mix):
    """The old mtime-only check missed a replacement source whose mtime is
    older than the cached background (e.g. a copied file keeping its date)."""
    src = _source(tmp_path)
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    with open(src, "wb") as f:
        f.write(b"a different, longer source")
    os.utime(src, (1_000_000, 1_000_000))
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    assert fake_mix == ["demucs", "demucs"]


def test_legacy_background_without_sidecar_is_reseparated(tmp_path, fake_mix):
    src = _source(tmp_path)
    (tmp_path / dub.BACKGROUND_FILENAME).write_bytes(b"old")
    os.utime(tmp_path / dub.BACKGROUND_FILENAME, None)
    dub.mix_original_background("t.wav", src, str(tmp_path), backend="demucs")
    assert fake_mix == ["demucs"]
