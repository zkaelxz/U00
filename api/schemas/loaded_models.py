"""Loaded-models panel (Settings): names, sizes and states only."""

from typing import List, Literal, Optional

from pydantic import BaseModel

__all__ = [
    "OllamaLoadedModel",
    "OllamaLoaded",
    "AppLoadedModel",
    "AppLoaded",
    "GpuMemory",
    "KeepFreeMemory",
    "MemoryHeadroom",
    "LoadedModels",
    "FreeAppModelsRequest",
    "ProcessMemoryRow",
    "ProcessMemory",
]


class OllamaLoadedModel(BaseModel):
    name: str
    size_bytes: int
    vram_bytes: int


class OllamaLoaded(BaseModel):
    state: Literal["running", "not_running", "not_local", "unavailable"]
    models: List[OllamaLoadedModel]


class AppLoadedModel(BaseModel):
    name: str
    kind: str
    device: Literal["GPU", "CPU", "Unknown"]


class AppLoaded(BaseModel):
    state: Literal["ok", "unavailable"]
    models: List[AppLoadedModel]


class GpuMemory(BaseModel):
    state: Literal["ok", "unknown"]
    name: Optional[str] = None
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    free_bytes: Optional[int] = None


class KeepFreeMemory(BaseModel):
    """Free and total memory, and the part Settings keeps free for other programs."""
    state: Literal["ok", "unknown"]
    total_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    reserved_bytes: int = 0


class MemoryHeadroom(BaseModel):
    vram: KeepFreeMemory
    ram: KeepFreeMemory


class LoadedModels(BaseModel):
    checked_at: str
    ollama: OllamaLoaded
    app: AppLoaded
    gpu: GpuMemory
    memory: MemoryHeadroom
    llama_cpp_running: bool
    gpu_job_running: bool


class FreeAppModelsRequest(BaseModel):
    confirm: bool = False


class ProcessMemoryRow(BaseModel):
    pid: int
    parent_pid: int
    role: str
    relation: Literal["this_server", "child", "orphan", "other"]
    ram_bytes: int
    vram_bytes: Optional[int] = None
    command: str


class ProcessMemory(BaseModel):
    checked_at: str
    state: Literal["ok", "unsupported", "unavailable"]
    server_pid: int
    processes: List[ProcessMemoryRow]
    gpu: GpuMemory
    vram_attributed_bytes: Optional[int] = None
