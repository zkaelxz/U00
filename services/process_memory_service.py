"""
services/process_memory_service.py -- which processes hold RAM and graphics
memory right now, read-only.

The Models panel only sees this process's own caches, so a worker child or a
leftover python.exe holding memory is invisible there. This lists Python,
Ollama and llama.cpp processes with their RAM and (where the OS says) VRAM,
and whether this server started each one. Responses carry no paths: command
lines are cut down to file names and anything secret-looking is redacted.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from engine_backends.shared import redact_secrets
from lib.proc import run_captured
from services import loaded_models_service

PROBE_TIMEOUT_SECONDS = 25
NVIDIA_SMI_TIMEOUT_SECONDS = 5
COMMAND_MAX_CHARS = 200
COMMAND_MAX_TOKENS = 12
RELEVANT_NAMES = ("python", "pythonw", "ollama", "llama-server", "llama_server")

# One PowerShell call for processes and the per-process GPU counters: the
# counters are the only per-process VRAM reading Windows gives for a card in
# display (WDDM) mode, where nvidia-smi prints N/A.
_WINDOWS_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$p=@(Get-CimInstance Win32_Process|Select-Object ProcessId,ParentProcessId,Name,CommandLine,WorkingSetSize);"
    "$g=@((Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage').CounterSamples|"
    "Select-Object InstanceName,CookedValue);"
    "@{procs=$p;gpu=$g}|ConvertTo-Json -Depth 3 -Compress"
)


def _is_relevant(name: str) -> bool:
    base = name.lower().removesuffix(".exe")
    return any(base == n or base.startswith(n) for n in RELEVANT_NAMES)


def _file_name(token: str) -> str:
    return token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or token


def sanitize_command(command: str) -> str:
    """File names only, no inline code after -c, secrets redacted, capped."""
    tokens = [t.strip('"') for t in re.findall(r'"[^"]*"|\S+', command or "")]
    kept = []
    for tok in tokens:
        if tok == "-c":
            # Everything after -c is inline code, which can hold anything.
            kept.append("-c …")
            break
        if "=" in tok and tok.startswith("-"):
            flag, _, value = tok.partition("=")
            tok = f"{flag}={_file_name(value)}"
        else:
            tok = _file_name(tok) if ("/" in tok or "\\" in tok) else tok
        kept.append(tok)
    text = " ".join(kept[:COMMAND_MAX_TOKENS]) + (" …" if len(kept) > COMMAND_MAX_TOKENS else "")
    text = redact_secrets(text)
    return text if len(text) <= COMMAND_MAX_CHARS else text[:COMMAND_MAX_CHARS - 1] + "…"


def _role(name: str, command: str) -> str:
    base = name.lower().removesuffix(".exe")
    if base.startswith("ollama"):
        return "Ollama"
    if base.startswith("llama"):
        return "llama.cpp server"
    low = command.lower()
    if "multiprocessing" in low and "spawn" in low:
        return "Job worker"
    if "voice_worker" in low:
        return "Voice worker"
    if re.search(r"(^|\s)-m\s+api(\s|$)", low):
        return "Baihe server"
    return "Python"


def _descendants(parents: dict, root: int) -> set:
    children: dict = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = set(), [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found and child != root:
                found.add(child)
                stack.append(child)
    return found


def build_rows(procs: list, vram_by_pid: dict, server_pid: int) -> list:
    """procs: dicts with pid, ppid, name, command, ram_bytes. Pure, for tests."""
    parents = {p["pid"]: p["ppid"] for p in procs}
    mine = _descendants(parents, server_pid)
    rows = []
    for p in procs:
        pid = p["pid"]
        if pid != server_pid and pid not in mine and not _is_relevant(p["name"]):
            continue
        if pid == server_pid:
            relation = "this_server"
        elif pid in mine:
            relation = "child"
        elif p["ppid"] not in parents:
            relation = "orphan"
        else:
            relation = "other"
        rows.append({"pid": pid, "parent_pid": p["ppid"], "role": _role(p["name"], p["command"]),
                     "relation": relation, "ram_bytes": p["ram_bytes"],
                     "vram_bytes": vram_by_pid.get(pid),
                     "command": sanitize_command(p["command"])})
    # What matters for the question "who holds the memory" first.
    rows.sort(key=lambda r: ((r["vram_bytes"] or 0) + (r["ram_bytes"] or 0)), reverse=True)
    return rows


def _nvidia_smi_vram() -> dict:
    """Per-pid VRAM from nvidia-smi; empty when it prints N/A (Windows display mode)."""
    result = run_captured(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                           "--format=csv,noheader,nounits"], timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    out = {}
    if result.timed_out or result.returncode != 0:
        return out
    for line in result.stdout.splitlines():
        pid, _, mem = (x.strip() for x in line.partition(","))
        if pid.isdigit() and mem.isdigit():
            out[int(pid)] = int(mem) * 1024 * 1024
    return out


def _read_windows() -> tuple:
    result = run_captured(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
                          timeout=PROBE_TIMEOUT_SECONDS)
    if result.timed_out or result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("process list unavailable")
    data = json.loads(result.stdout)
    procs = [{"pid": int(p["ProcessId"]), "ppid": int(p["ParentProcessId"] or 0),
              "name": str(p["Name"] or ""), "command": str(p["CommandLine"] or ""),
              "ram_bytes": int(p["WorkingSetSize"] or 0)}
             for p in _as_list(data.get("procs")) if p.get("ProcessId") is not None]
    vram: dict = {}
    for sample in _as_list(data.get("gpu")):
        match = re.match(r"pid_(\d+)_", str(sample.get("InstanceName") or ""))
        if match:
            pid = int(match.group(1))
            vram[pid] = vram.get(pid, 0) + int(sample.get("CookedValue") or 0)
    return procs, vram


def _as_list(value) -> list:
    # ConvertTo-Json turns a one-item array into a bare object.
    return value if isinstance(value, list) else ([value] if value else [])


def _read_proc_fs() -> tuple:
    procs = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status", encoding="utf-8", errors="replace") as f:
                fields = dict(line.split(":", 1) for line in f if ":" in line)
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                command = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            rss_kb = int(fields.get("VmRSS", "0 kB").split()[0])
            procs.append({"pid": int(entry), "ppid": int(fields["PPid"].strip()),
                          "name": fields["Name"].strip(), "command": command,
                          "ram_bytes": rss_kb * 1024})
        except (OSError, KeyError, ValueError):
            continue   # the process ended while being read
    return procs, {}


def get_process_memory() -> dict:
    out = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "state": "ok", "server_pid": os.getpid(), "processes": [],
           "gpu": loaded_models_service._guarded(loaded_models_service._gpu_row, {"state": "unknown"}),
           "vram_attributed_bytes": None}
    try:
        if sys.platform == "win32":
            procs, vram = _read_windows()
        elif sys.platform.startswith("linux"):
            procs, vram = _read_proc_fs()
        else:
            out["state"] = "unsupported"
            return out
        try:
            for pid, size in _nvidia_smi_vram().items():
                vram.setdefault(pid, size)
        except Exception:
            pass   # no nvidia-smi: the OS counters (if any) stand alone
        out["processes"] = build_rows(procs, vram, os.getpid())
        out["vram_attributed_bytes"] = sum(vram.values()) if vram else None
    except Exception:
        out["state"] = "unavailable"
        out["processes"] = []
    return out
