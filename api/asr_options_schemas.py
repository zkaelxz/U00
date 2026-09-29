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
    moss_experimental: bool
    # Whether the moss_transcribe_diarize package is importable on the PC.
    moss_installed: bool


class AsrOptionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qwen_asr_batch_size: Optional[int] = Field(None, ge=1, le=16)
    moss_experimental: Optional[bool] = None
