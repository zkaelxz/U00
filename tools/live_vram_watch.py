"""Samples GPU memory and Ollama's GPU share while a Live session runs: `python -m tools.live_vram_watch [--minutes 30] [--every 10]`.

Start it, then start Live with Whisper and an Ollama translator. It reads
nvidia-smi and Ollama's /api/ps (loopback only) and prints a summary: peak GPU
memory, and any sample where a loaded Ollama model was not fully on the GPU,
which is when Live's captions start to fall behind. It changes nothing.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/live_vram_watch.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import http

SMI_TIMEOUT_SECONDS = 5
OLLAMA_TIMEOUT_SECONDS = 3
OLLAMA_PS_MAX_BYTES = 1_000_000


def gpu_memory_mib() -> list:
    """(used, total) MiB per GPU; [] when nvidia-smi is missing or fails."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=SMI_TIMEOUT_SECONDS, check=True).stdout
        return [tuple(int(v) for v in line.split(",")) for line in out.strip().splitlines()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def ollama_models(base_url: str) -> list:
    """(name, share of the model held on the GPU) per loaded model; [] when Ollama is not running."""
    try:
        resp = http.get(f"{base_url}/api/ps", timeout=OLLAMA_TIMEOUT_SECONDS, max_bytes=OLLAMA_PS_MAX_BYTES,
                        guard=None, trust_env=False, max_error_bytes=1)
        loaded = json.loads(resp.body).get("models") or [] if resp.ok else []
    except Exception:
        return []
    rows = []
    for item in loaded:
        size = int(item.get("size") or 0)
        on_gpu = min(int(item.get("size_vram") or 0), size)
        rows.append((str(item.get("name") or "?")[:80], on_gpu / size if size else 0.0))
    return rows


def summarize(samples: list) -> dict:
    """samples: [{"gpu": [(used, total)], "ollama": [(name, gpu_share)]}]."""
    peak_used = max((used for s in samples for used, _total in s["gpu"]), default=None)
    total = max((total for s in samples for _used, total in s["gpu"]), default=None)
    spilled = sorted({name for s in samples for name, share in s["ollama"] if share < 0.999})
    return {"samples": len(samples), "peak_used_mib": peak_used, "total_mib": total,
            "spilled_models": spilled}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--every", type=float, default=10, help="seconds between samples")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args(argv)
    host = args.ollama_url.split("//")[-1].split(":")[0].split("/")[0]
    if host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--ollama-url must be on this PC")
    samples = []
    end = time.monotonic() + args.minutes * 60
    while time.monotonic() < end:
        samples.append({"gpu": gpu_memory_mib(), "ollama": ollama_models(args.ollama_url)})
        time.sleep(args.every)
    result = summarize(samples)
    print(f"{result['samples']} samples")
    if result["peak_used_mib"] is None:
        print("nvidia-smi gave no reading: GPU memory unknown")
    else:
        print(f"Peak GPU memory: {result['peak_used_mib']} of {result['total_mib']} MiB")
    if result["spilled_models"]:
        print("Not fully on the GPU at some point: " + ", ".join(result["spilled_models"]))
    else:
        print("Every loaded Ollama model stayed fully on the GPU (or none was loaded)")
    return 1 if result["spilled_models"] else 0


if __name__ == "__main__":
    sys.exit(main())
