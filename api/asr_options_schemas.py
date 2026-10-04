"""
api/asr_options_schemas.py -- request/response models for the experimental
transcription settings (api/routers/asr_options_routes.py, Steps 103/104).
Kept out of api/schemas.py so this could be built alongside another branch
editing that file; the shared ErrorResponse still lives there.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AsrOptions(BaseModel):
    qwen_asr_batch_size: int
    qwen_asr_batch_min: int
    qwen_asr_batch_max: int
    # The installed qwen-asr version (None if not installed) and whether
    # batching can run with it (only the tested version batches).
    qwen_asr_version: Optional[str] = None
    qwen_asr_batching_available: bool = False
    moss_experimental: bool
    qwen_vad_refine_timing: bool = False
    # Whether the moss_transcribe_diarize package is importable on the PC.
    moss_installed: bool


class AsrOptionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qwen_asr_batch_size: Optional[int] = Field(None, ge=1, le=16)
    moss_experimental: Optional[bool] = None
    qwen_vad_refine_timing: Optional[bool] = None
