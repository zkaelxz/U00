"""Loaded-models panel (Settings): names, sizes and states only."""

from typing import List, Literal, Optional

from pydantic import BaseModel


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


class LoadedModels(BaseModel):
    checked_at: str
    ollama: OllamaLoaded
    app: AppLoaded
    gpu: GpuMemory
    llama_cpp_running: bool
    gpu_job_running: bool


class FreeAppModelsRequest(BaseModel):
    confirm: bool = False
