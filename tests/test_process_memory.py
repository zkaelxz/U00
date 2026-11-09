"""Memory-by-process panel: which processes are listed, who started them, and
that no path or secret leaves in a command line."""

import sys

import pytest

from services import process_memory_service as svc

GB = 1024 ** 3


def _p(pid, ppid, name, command="", ram=1):
    return {"pid": pid, "ppid": ppid, "name": name, "command": command, "ram_bytes": ram}


def test_children_orphans_and_other_programs_are_told_apart():
    procs = [
        _p(100, 1, "python.exe", r"C:\Baihe\python.exe -m api", GB),
        _p(101, 100, "python.exe", r"C:\Baihe\python.exe -c from multiprocessing.spawn import spawn_main", 5 * GB),
        _p(200, 999, "python.exe", r"D:\x\python.exe -m something", 4 * GB),
        _p(300, 100, "chrome.exe", "chrome"),
        _p(400, 100, "ffmpeg.exe", "ffmpeg"),
        _p(500, 50, "notepad.exe", "notepad"),
    ]
    rows = {r["pid"]: r for r in svc.build_rows(procs, {101: 4 * GB}, server_pid=100)}
    assert rows[100]["relation"] == "this_server" and rows[100]["role"] == "Baihe server"
    assert rows[101]["relation"] == "child" and rows[101]["role"] == "Job worker"
    assert rows[101]["vram_bytes"] == 4 * GB
    assert rows[200]["relation"] == "orphan"
    assert 500 not in rows            # unrelated program never listed
    assert 300 in rows and 400 in rows  # children of the server are always shown
    assert list(rows)[0] == 101       # biggest holder first


def test_command_line_has_no_paths_or_secrets():
    cmd = r'"C:\Users\Someone\venv\python.exe" -m api --data=C:\Users\Someone\lib --api_key=sk-abc123456789abcdef'
    out = svc.sanitize_command(cmd)
    assert "Users" not in out and "Someone" not in out
    assert "sk-abc123456789abcdef" not in out
    assert out.startswith("python.exe -m api")


def test_inline_code_is_dropped_and_length_capped():
    out = svc.sanitize_command("python -c print('secret-ish body') " + "x " * 500)
    assert "secret-ish" not in out and len(out) <= svc.COMMAND_MAX_CHARS


def test_get_process_memory_states(monkeypatch):
    monkeypatch.setattr(svc.loaded_models_service, "_gpu_row", lambda: {"state": "unknown"})
    monkeypatch.setattr(svc, "_nvidia_smi_vram", lambda: {})
    monkeypatch.setattr(svc.sys, "platform", "darwin")
    assert svc.get_process_memory()["state"] == "unsupported"

    monkeypatch.setattr(svc.sys, "platform", "linux")
    monkeypatch.setattr(svc, "_read_proc_fs", lambda: (_ for _ in ()).throw(OSError("no /proc")))
    assert svc.get_process_memory()["state"] == "unavailable"

    monkeypatch.setattr(svc, "_read_proc_fs", lambda: ([_p(svc.os.getpid(), 1, "python3", "python -m api", 7)], {}))
    monkeypatch.setattr(svc, "_nvidia_smi_vram", lambda: {svc.os.getpid(): 2 * GB})
    out = svc.get_process_memory()
    assert out["state"] == "ok" and out["processes"][0]["vram_bytes"] == 2 * GB
    assert out["vram_attributed_bytes"] == 2 * GB


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_real_proc_read_finds_this_process():
    procs, _ = svc._read_proc_fs()
    assert any(p["pid"] == svc.os.getpid() and p["ram_bytes"] > 0 for p in procs)
