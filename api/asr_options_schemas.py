"""
api/asr_options_schemas.py -- request/response models for the experimental
transcription settings (api/routers/asr_options_routes.py).
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AsrOptions(BaseModel):
    qwen_asr_batch_size: int
    qwen_asr_batch_min: int
    qwen_asr_batch_max: int
    # The installed transformers version (None if not installed) and whether
    # Qwen3-ASR (and so batching) can run with it: transformers 5.15 or newer.
    qwen_asr_version: Optional[str] = None
    qwen_asr_batching_available: bool = False
    qwen_vad_refine_timing: bool = False
    mixed_languages: bool = False
    voice_detector: Literal["auto", "asmr", "standard"] = "auto"
    # Booleans only: the model's location and URL stay on the PC.
    asmr_vad_onnxruntime_installed: bool = False
    asmr_vad_model_downloaded: bool = False
    asmr_vad_download_job_id: str = ""


class AsrVadDownloadStarted(BaseModel):
    job_id: str
    started: bool


class AsrOptionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qwen_asr_batch_size: Optional[int] = Field(None, ge=1, le=16)
    qwen_vad_refine_timing: Optional[bool] = None
    mixed_languages: Optional[bool] = None
    voice_detector: Optional[Literal["auto", "asmr", "standard"]] = None
