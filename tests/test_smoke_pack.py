"""scripts/smoke_pack.py: the comparison logic and the init/run commands, with a fake run_clip
(no GPU, no models)."""
import copy
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("smoke_pack", os.path.join(ROOT, "scripts", "smoke_pack.py"))
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)


def _lines(n=20):
    return [{"start": i * 3.0, "end": i * 3.0 + 2.5, "text": f"这是第{i}句测试的台词内容"} for i in range(n)]


def _result(**over):
    base = {"lines": _lines(), "speaker_count": 2,
            "stage_seconds": {"transcribe": 60.0, "diarize": 20.0, "model_load": 3.0},
            "devices": {"transcribe": "Using GPU (float16)", "diarize": "gpu"}, "notes": []}
    base.update(over)
    return base


PROFILE = {"diarization": True, "tolerances": {}}


def _expected(**over):
    return {**_result(), "approved": True, **over}


def _statuses(res):
    return {c["name"]: c["status"] for c in res["checks"]}


def _run(actual, profile=PROFILE, expected=None):
    return sp.compare(expected or _expected(), actual, profile)


def test_identical_run_passes():
    res = _run(_result())
    assert res["status"] == sp.PASS
    assert sp.exit_code_for(res["status"]) == 0


def test_text_cer_fail_and_tolerance():
    changed = _result()
    changed["lines"] = [dict(ln, text=ln["text"][:-3] + "完全不同") for ln in changed["lines"]]
    assert _statuses(_run(changed))["Text matches baseline"] == sp.FAIL
    loose = {"diarization": True, "tolerances": {"max_cer": 0.9}}
    assert _statuses(_run(changed, loose))["Text matches baseline"] == sp.PASS


def test_line_count_limit():
    short = _result(lines=_lines(14))
    assert _statuses(_run(short))["Line count"] == sp.FAIL
    assert _statuses(_run(_result(lines=_lines(18))))["Line count"] == sp.PASS


def test_long_lines_beyond_tolerance():
    long_ones = [dict(ln, text="字" * 41) for ln in _lines()[:3]]
    res = _run(_result(lines=long_ones + _lines()[3:]))
    assert _statuses(res)["No over-long lines"] == sp.FAIL
    one = _run(_result(lines=long_ones[:1] + _lines()[1:]))
    assert _statuses(one)["No over-long lines"] == sp.PASS


def test_baseline_long_lines_are_allowed():
    base_lines = [dict(ln, text="字" * 41) for ln in _lines()[:3]] + _lines()[3:]
    res = sp.compare(_expected(lines=base_lines), _result(lines=copy.deepcopy(base_lines)), PROFILE)
    assert _statuses(res)["No over-long lines"] == sp.PASS


@pytest.mark.parametrize("mutate", [
    lambda ls: ls[3].update(end=ls[3]["start"]),                    # zero length
    lambda ls: ls[3].update(end=ls[3]["start"] - 1),                # negative
    lambda ls: ls.__setitem__(slice(4, 6), [ls[5], ls[4]]),         # out of order
])
def test_timing_sanity_fails(mutate):
    lines = _lines()
    mutate(lines)
    assert _statuses(_run(_result(lines=lines)))["Times in order, no empty lines"] == sp.FAIL


def test_overlap_limit():
    lines = _lines()
    lines[2] = dict(lines[2], end=lines[3]["start"] + 0.5)
    assert _statuses(_run(_result(lines=lines)))["No overlaps"] == sp.FAIL
    lines[2] = dict(lines[2], end=lines[3]["start"] + 0.2)
    assert _statuses(_run(_result(lines=lines)))["No overlaps"] == sp.PASS


def test_speaker_count_and_slack():
    assert _statuses(_run(_result(speaker_count=3)))["Speaker count"] == sp.FAIL
    loose = {"diarization": True, "tolerances": {"speaker_count_diff": 1}}
    assert _statuses(_run(_result(speaker_count=3), loose))["Speaker count"] == sp.PASS
    assert "Speaker count" not in _statuses(_run(_result(speaker_count=9), {"tolerances": {}}))


def test_device_downgrade_is_hard_fail_with_reason():
    res = _run(_result(devices={"transcribe": "Using CPU (int8)", "diarize": "gpu"}))
    assert res["status"] == sp.FAIL
    detail = next(c["detail"] for c in res["checks"] if c["name"] == "Device: transcribe")
    assert "GPU" in detail and "CPU" in detail
    fallback = _run(_result(devices={"transcribe": "GPU unavailable (CUDA error); using CPU",
                                     "diarize": "gpu"}))
    assert _statuses(fallback)["Device: transcribe"] == sp.FAIL


def test_compute_type_change_is_only_a_warning():
    res = _run(_result(devices={"transcribe": "Using GPU (int8_float16)", "diarize": "gpu"}))
    assert _statuses(res)["Device: transcribe"] == sp.WARN
    assert sp.exit_code_for(res["status"]) == 3


def test_speed_warn_over_limit_and_ignores_fast_stages():
    slow = _result(stage_seconds={"transcribe": 90.0, "diarize": 20.0, "model_load": 99.0})
    st = _statuses(_run(slow))
    assert st["Speed: transcribe"] == sp.WARN
    assert "Speed: model_load" not in st
    ok = _result(stage_seconds={"transcribe": 80.0, "diarize": 20.0})
    assert _statuses(_run(ok))["Speed: transcribe"] == sp.PASS
    noisy = _expected(stage_seconds={"transcribe": 60.0, "diarize": 2.0})
    st = _statuses(_run(_result(stage_seconds={"transcribe": 60.0, "diarize": 9.0}), expected=noisy))
    assert "Speed: diarize" not in st


def test_unapproved_baseline_warns():
    res = _run(_result(), expected=_expected(approved=False))
    assert res["status"] == sp.WARN


def test_version_changes_listed():
    old = {"torch": "2.4.0", "numpy": "1.26.0", "demucs": None, "python": "3.11.9"}
    new = {"torch": "2.5.0", "numpy": "1.26.0", "demucs": "4.0.1", "python": "3.11.9"}
    assert sp.version_changes(old, new) == [("demucs", None, "4.0.1"), ("torch", "2.4.0", "2.5.0")]
    res = sp.compare(_expected(), _result(devices={"transcribe": "Using CPU (int8)", "diarize": "gpu"}),
                     PROFILE, old, new)
    text = sp.format_report(res, "demo")
    assert "torch: 2.4.0 -> 2.5.0" in text and "demucs: not installed -> 4.0.1" in text
    assert "FAIL" in text and "Result: FAIL" in text


def test_versions_command_prints_json(capsys):
    assert sp.main(["versions"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {"python", "cuda", "faster-whisper", "torch", "yt-dlp", "pillow"} <= set(data)


def test_scrub_removes_paths_and_keys():
    text = sp.scrub(r"cannot open C:\Users\alice\Videos\clip.wav and /home/bob/x/y.bin "
                    "with sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "alice" not in text and "bob" not in text and "sk-abcdef" not in text
    assert "clip.wav" in text and "y.bin" in text


def _fake_run(calls=None, **over):
    def fake(profile, audio_path, expected_texts=None):
        if calls is not None:
            calls.append((dict(profile), audio_path, expected_texts))
        return _result(**over)
    return fake


def _clip(tmp_path):
    clip = tmp_path / "private folder" / "clip.wav"
    clip.parent.mkdir()
    clip.write_bytes(b"RIFF")
    return clip


def test_init_then_run_pass(tmp_path, capsys):
    root = tmp_path / "pack"
    clip = _clip(tmp_path)
    assert sp.main(["init", "--audio", str(clip), "--language", "zh", "--diarization"],
                   run=_fake_run(), root=root) == 0
    assert "approved" in capsys.readouterr().out
    pack = root / "clip"
    profile = json.loads((pack / "profile.json").read_text(encoding="utf-8"))
    expected = json.loads((pack / "expected.json").read_text(encoding="utf-8"))
    assert profile["audio_file"] == "clip.wav" and profile["diarization"] is True
    assert profile["tolerances"]["max_cer"] == 0.08
    assert expected["approved"] is False and expected["versions"]["python"]

    # not approved yet: only a warning
    assert sp.main(["run"], run=_fake_run(), root=root) == 3
    expected["approved"] = True
    (pack / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    calls = []
    assert sp.main(["run", "--name", "clip"], run=_fake_run(calls), root=root) == 0
    assert calls[0][1] == str(pack / "clip.wav")
    last = (pack / "last_run.json").read_text(encoding="utf-8")
    assert json.loads(last)["result"]["status"] == "PASS"
    assert str(tmp_path) not in last and "private folder" not in last
    assert str(tmp_path) not in (pack / "expected.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in (pack / "profile.json").read_text(encoding="utf-8")


def test_run_fail_and_update_baseline(tmp_path, capsys):
    root = tmp_path / "pack"
    sp.main(["init", "--audio", str(_clip(tmp_path)), "--language", "zh"],
            run=_fake_run(), root=root)
    pack = root / "clip"
    expected = json.loads((pack / "expected.json").read_text(encoding="utf-8"))
    expected["approved"] = True
    (pack / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    cpu = _fake_run(devices={"transcribe": "Using CPU (int8)", "diarize": "gpu"})
    assert sp.main(["run"], run=cpu, root=root) == 1
    assert sp.main(["run", "--update-baseline"], run=cpu, root=root) == 1
    assert (pack / "expected.previous.json").exists()
    assert sp.main(["run"], run=cpu, root=root) == 0


def test_run_pipeline_exception_is_a_redacted_fail(tmp_path, capsys):
    root = tmp_path / "pack"
    sp.main(["init", "--audio", str(_clip(tmp_path)), "--language", "zh"], run=_fake_run(), root=root)

    def boom(profile, audio_path, expected_texts=None):
        raise RuntimeError(f"bad file {tmp_path}/models/x.bin token=hf_abcdefghijklmnopqrstuvwxyz1234")
    assert sp.main(["run"], run=boom, root=root) == 1
    report = (root / "clip" / "last_run.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in report and "hf_abcdefgh" not in report


def test_error_exit_codes(tmp_path, capsys):
    root = tmp_path / "pack"
    assert sp.main(["init", "--audio", str(tmp_path / "missing.wav"), "--language", "zh"],
                   run=_fake_run(), root=root) == 2
    assert sp.main(["run", "--name", "nothing"], run=_fake_run(), root=root) == 2
    assert sp.main(["run"], run=_fake_run(), root=root) == 2
    clip = _clip(tmp_path)
    sp.main(["init", "--audio", str(clip), "--language", "zh"], run=_fake_run(), root=root)
    assert sp.main(["init", "--audio", str(clip), "--language", "zh"], run=_fake_run(), root=root) == 2
    (root / "clip" / "clip.wav").unlink()
    assert sp.main(["run"], run=_fake_run(), root=root) == 2
    assert sp.main(["run", "--name", "../x"], run=_fake_run(), root=root) == 2


def test_init_pipeline_failure_is_error_exit(tmp_path):
    def boom(profile, audio_path, expected_texts=None):
        raise RuntimeError("model missing")
    assert sp.main(["init", "--audio", str(_clip(tmp_path)), "--language", "zh"],
                   run=boom, root=tmp_path / "pack") == 2


def test_stage_recorder_durations():
    now = [0.0]
    rec = sp.StageRecorder(clock=lambda: now[0])
    rec.stage("Loading Whisper model large-v3...")
    now[0] = 4.0
    rec.progress(0.0, "Transcribing... starting")
    now[0] = 10.0
    rec.progress(0.5, "Transcribing... 50%")
    now[0] = 70.0
    assert rec.durations(70.0) == {"model_load": 4.0, "transcribe": 66.0}
    rec.progress(0.1, "Separating vocals on GPU, 10%")
    assert rec.separation_device == "gpu"


@pytest.mark.parametrize("configured", ["/models/whisper-small", None])
def test_run_clip_passes_the_offline_whisper_folder(tmp_path, monkeypatch, configured):
    from services import settings_service
    from services import transcribe_service as ts
    monkeypatch.setattr(settings_service, "get_whisper_model_path", lambda: configured)
    seen = {}

    def fake_pipeline(rep, *args, **kwargs):
        seen.update(kwargs)
        return {"lines": [], "segments": []}

    monkeypatch.setattr(ts, "_transcribe_pipeline", fake_pipeline)
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    sp.run_clip({"language": "zh", "use_gpu": False}, str(clip))
    assert seen["local_model_path"] == configured
