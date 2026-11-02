"""voice_venv / voice_worker: the venv layout and the child protocol, with real child
processes. The "venv" is this interpreter and the engine is a fake worker script, so no
engine package or model is ever loaded."""
import json
import os
import sys
import time

import pytest

import voice_venv
import voice_worker

FAKE_WORKER = r'''
import json, sys, time
spec = json.load(open(sys.argv[1], encoding="utf-8"))
prefix = "@@voice-" + spec["nonce"] + " "
def emit(event):
    sys.stdout.write("\n" + prefix + json.dumps(event) + "\n"); sys.stdout.flush()
mode = spec["options"]["mode"]
print("loading weights... 100%")
emit({"loaded": True})
if mode == "hang":
    time.sleep(60)
items = spec["items"]
if mode == "mixed":
    print("@@voice-" + "0" * 32 + ' {"done": "b"}')    # forged: wrong nonce
    print(prefix + "{not json")
    emit({"done": "nobody-asked"})
    emit({"done": "a", "extra": 1})
    emit({"done": []})
    with open(items[0]["out"], "wb") as f:
        f.write(b"RIFF")
    emit({"done": "a"})
    emit({"done": "a"})                                  # answered twice
    emit({"done": "b"})                                  # no file written
    emit({"failed": "c", "error": "x" * 1000})
'''


@pytest.fixture
def fake_venv(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    monkeypatch.setattr(voice_venv, "WORKER_SCRIPT", str(script))
    monkeypatch.setattr(voice_venv, "venv_python", lambda engine: sys.executable)
    work = tmp_path / "work"
    work.mkdir()
    return work


def _items(work, *ids):
    return [{"id": i, "text": "你好", "out": str(work / f"{i}.wav")} for i in ids]


def test_venv_lives_under_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    assert voice_venv.venv_dir("qwen3_tts") == os.path.join(str(tmp_path), "voice_engines",
                                                            "qwen3_tts")
    for bad in ("", "../x", "Qwen", "a/b", "a" * 41):
        with pytest.raises(ValueError):
            voice_venv.venv_dir(bad)


def test_ready_needs_the_python_and_the_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    assert not voice_venv.is_ready("qwen3_tts")
    python = voice_venv.venv_python("qwen3_tts")
    os.makedirs(os.path.dirname(python))
    open(python, "w").close()
    assert not voice_venv.is_ready("qwen3_tts")     # an interrupted install
    open(os.path.join(voice_venv.venv_dir("qwen3_tts"), voice_venv.READY_MARKER), "w").close()
    assert voice_venv.is_ready("qwen3_tts")


def test_worker_speaks_each_item_and_keeps_going_after_a_failure():
    events, spoken = [], []

    def load(spec):
        def speak(item):
            if item["id"] == "bad":
                raise RuntimeError("no voice")
            spoken.append(item["id"])
        return speak

    spec = {"engine": "fake", "items": [{"id": "a"}, {"id": "bad"}, {"id": "c"}]}
    voice_worker.run(spec, events.append, adapters={"fake": load})
    assert events == [{"loaded": True}, {"done": "a"},
                      {"failed": "bad", "error": "RuntimeError: no voice"}, {"done": "c"}]
    assert spoken == ["a", "c"]


def test_worker_stops_when_the_model_cannot_load():
    events = []

    def load(spec):
        raise ImportError("transformers too new")

    voice_worker.run({"engine": "fake", "items": [{"id": "a"}]}, events.append,
                     adapters={"fake": load})
    assert events == [{"fatal": "ImportError: transformers too new"}]
    events.clear()
    voice_worker.run({"engine": "other", "items": []}, events.append, adapters={"fake": load})
    assert events == [{"fatal": "Unknown voice engine."}]


def test_real_worker_runs_isolated_and_reports_an_unknown_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_venv, "venv_python", lambda engine: sys.executable)
    out = list(voice_venv.run_batch("no_such_engine", _items(tmp_path, "a"), str(tmp_path),
                                    timeout=60))
    assert out[0] == {"fatal": voice_venv.ENGINE_FAILED, "detail": "Unknown voice engine."}
    assert out[-1]["returncode"] == 0 and out[-1]["unanswered"] == ["a"]


def test_only_checked_events_for_asked_ids_get_through(fake_venv):
    out = list(voice_venv.run_batch("fake", _items(fake_venv, "a", "b", "c", "d"),
                                    str(fake_venv), timeout=60, options={"mode": "mixed"}))
    events, end = out[:-1], out[-1]
    assert events == [{"loaded": True}, {"done": "a"},
                      {"failed": "b", "error": voice_venv.NO_AUDIO, "detail": ""},
                      {"failed": "c", "error": voice_venv.LINE_FAILED,
                       "detail": "x" * voice_worker.ERROR_MAX_CHARS}]
    # nobody-asked, the extra key, the list id, the bad JSON and the second "a"; the
    # forged line is noise.
    assert end["dropped"] == 5
    assert end["unanswered"] == ["d"] and end["returncode"] == 0
    assert os.listdir(fake_venv) == ["a.wav"]       # the spec is gone


def test_cancel_kills_the_child_and_removes_the_spec(fake_venv):
    started = time.monotonic()
    seen = []

    def cancel():
        return bool(seen)

    out = []
    for event in voice_venv.run_batch("fake", _items(fake_venv, "a"), str(fake_venv),
                                      timeout=60, cancel=cancel, options={"mode": "hang"}):
        seen.append(event)
        out.append(event)
    assert out[0] == {"loaded": True}
    assert out[-1]["cancelled"] and out[-1]["unanswered"] == ["a"]
    assert time.monotonic() - started < 30
    assert os.listdir(fake_venv) == []


def test_stopping_early_kills_the_child(fake_venv):
    pids = []
    batch = voice_venv.run_batch("fake", _items(fake_venv, "a"), str(fake_venv), timeout=60,
                                 options={"mode": "hang"}, on_start=pids.append)
    assert next(batch) == {"loaded": True}
    batch.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pids[0], 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the worker outlived the closed batch")
    assert os.listdir(fake_venv) == []


def test_an_old_file_at_an_out_path_does_not_count_as_spoken(fake_venv):
    (fake_venv / "b.wav").write_bytes(b"RIFF from an earlier run")
    out = list(voice_venv.run_batch("fake", _items(fake_venv, "a", "b"), str(fake_venv), 60,
                                    options={"mode": "mixed"}))
    assert {"failed": "b", "error": voice_venv.NO_AUDIO, "detail": ""} in out


def test_a_missing_venv_python_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError) as err:
        list(voice_venv.run_batch("qwen3_tts", _items(tmp_path, "a"), str(tmp_path), 60))
    assert str(err.value) == voice_venv.ENGINE_FAILED
    assert os.listdir(tmp_path) == []


def test_ids_must_be_unique(fake_venv):
    with pytest.raises(ValueError):
        list(voice_venv.run_batch("fake", _items(fake_venv, "a", "a"), str(fake_venv), 60))


def test_text_and_paths_stay_off_the_command_line(fake_venv, monkeypatch):
    seen = {}
    real = voice_venv.proc.stream_tree

    def spy(cmd, *a, **kw):
        seen["cmd"] = cmd
        return real(cmd, *a, **kw)

    monkeypatch.setattr(voice_venv.proc, "stream_tree", spy)
    list(voice_venv.run_batch("fake", _items(fake_venv, "a"), str(fake_venv), 60,
                              options={"mode": "quiet"}))
    assert seen["cmd"][:4] == [sys.executable, "-I", "-X", "utf8"]
    assert not any("你好" in part or "a.wav" in part for part in seen["cmd"])
    assert json.dumps(seen["cmd"]).count("voice_spec_") == 1


class _FakeQwenModel:
    def __init__(self, calls):
        self.calls = calls

    def create_voice_clone_prompt(self, ref_audio, ref_text, x_vector_only_mode):
        self.calls.append(("prompt", ref_audio, ref_text, x_vector_only_mode))
        return [f"prompt:{ref_audio}"]

    def generate_voice_clone(self, text, language, voice_clone_prompt):
        self.calls.append(("speak", text, language, voice_clone_prompt))
        return [[0.0, 0.1]], 24000


def _fake_qwen_stack(monkeypatch, major):
    import types
    calls, written = [], []
    torch = types.SimpleNamespace(
        bfloat16="bf16", float16="fp16", float32="fp32",
        cuda=types.SimpleNamespace(is_available=lambda: True,
                                   # True on Volta too: it counts emulated bfloat16.
                                   is_bf16_supported=lambda: True,
                                   get_device_capability=lambda: (major, 0)))
    qwen_tts = types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(
        from_pretrained=lambda name, **kw: (calls.append(("load", name, kw)),
                                            _FakeQwenModel(calls))[1]))
    soundfile = types.SimpleNamespace(write=lambda path, wav, rate: written.append((path, rate)))
    for name, module in (("torch", torch), ("qwen_tts", qwen_tts), ("soundfile", soundfile)):
        monkeypatch.setitem(sys.modules, name, module)
    return calls, written


def test_qwen3_tts_adapter_builds_each_voice_once_and_maps_languages(monkeypatch):
    calls, written = _fake_qwen_stack(monkeypatch, major=8)
    items = [{"id": "0", "text": "你好", "language": "zh", "ref_audio": "a.wav", "ref_text": "嗯",
              "out": "0.wav"},
             {"id": "1", "text": "Hi", "language": "en", "ref_audio": "a.wav", "ref_text": "嗯",
              "out": "1.wav"},
             {"id": "2", "text": "안녕", "language": "xx", "ref_audio": "b.wav", "ref_text": "",
              "out": "2.wav"}]
    events = []
    voice_worker.run({"engine": "qwen3_tts", "options": {}, "items": items}, events.append)
    assert events == [{"loaded": True}, {"done": "0"}, {"done": "1"}, {"done": "2"}]
    assert calls[0] == ("load", voice_worker.QWEN3_TTS_MODEL,
                        {"device_map": "cuda:0", "dtype": "bf16"})
    prompts = [c for c in calls if c[0] == "prompt"]
    # One prompt per voice; a clip with no transcript uses only the speaker embedding.
    assert prompts == [("prompt", "a.wav", "嗯", False), ("prompt", "b.wav", None, True)]
    assert [c[2] for c in calls if c[0] == "speak"] == ["Chinese", "English", "Auto"]
    assert written == [("0.wav", 24000), ("1.wav", 24000), ("2.wav", 24000)]


def test_qwen3_tts_uses_half_precision_where_bfloat16_is_missing(monkeypatch):
    calls, _ = _fake_qwen_stack(monkeypatch, major=7)    # a Volta card such as a V100
    voice_worker.run({"engine": "qwen3_tts", "items": []}, lambda e: None)
    assert calls[0][2]["dtype"] == "fp16"


def test_the_worker_exits_once_nobody_reads_its_output(monkeypatch):
    monkeypatch.setattr(voice_worker, "PARENT_CHECK_SECONDS", 0)
    writes = []

    def write(text):
        writes.append(text)
        if len(writes) == 3:
            raise BrokenPipeError

    def exit_(code):
        raise SystemExit(code)
    monkeypatch.setattr(voice_worker.os, "_exit", exit_)
    with pytest.raises(SystemExit):
        voice_worker._exit_when_orphaned(write)
    assert writes == ["\n"] * 3
