"""The text shown for a job waiting on a GPU another program is using."""


def external_gpu_wait_message(load: dict):
    """The waiting message when a program Baihe did not start is using the
    GPU, or None if the reading shows no such load. Numbers only: nvidia-smi
    is queried for totals, never for process names, paths or command lines."""
    import diagnostics_torch
    util = load.get("utilization_percent") or 0
    free_mb = load.get("memory_free_mb")
    total_mb = load.get("memory_total_mb") or 0
    busy_util = util >= diagnostics_torch.EXTERNAL_GPU_BUSY_UTIL_PERCENT
    busy_mem = free_mb is not None and free_mb < diagnostics_torch.EXTERNAL_GPU_BUSY_MIN_FREE_MB
    if not (busy_util or busy_mem):
        return None
    used_gb = (total_mb - (free_mb or 0)) / 1024
    usage = f"about {used_gb:.1f} GB of {total_mb / 1024:.1f} GB in use"
    if busy_util and not busy_mem:
        usage += f", GPU about {util:.0f}% busy"
    return ("Waiting for the GPU: another program is using it (" + usage + "). "
            "Close GPU-heavy apps such as games, video editors or other AI tools, "
            "or turn off GPU use in Settings.")
