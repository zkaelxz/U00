"""api/schemas/voices.py -- Voices screen shapes: enrolled voice prints and the clear, retrain and remove-sample bodies."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.schemas.common import ConfirmRequest

__all__ = ["VoiceSample", "VoiceEntry", "VoiceListResponse", "VoiceChangeResult",
           "VoiceClearRequest", "VoiceRetrainRequest", "VoiceRemoveSampleRequest"]


class VoiceSample(BaseModel):
    drama_id: int
    title: str
    speaker_label: str
    similarity: Optional[float] = None


class VoiceEntry(BaseModel):
    series_id: int
    series_name: str
    character_id: int
    character_name: str
    sample_count: int
    linked: List[VoiceSample]
    average_similarity: Optional[float] = None
    lowest_similarity: Optional[float] = None
    rebuildable: int


class VoiceListResponse(BaseModel):
    items: List[VoiceEntry]


class VoiceChangeResult(BaseModel):
    series_id: int
    character_id: int
    sample_count: int


class VoiceClearRequest(ConfirmRequest):
    pass


class VoiceRetrainRequest(ConfirmRequest):
    pass


class VoiceRemoveSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drama_id: int = Field(ge=1, le=2147483647)
    speaker_label: str = Field(min_length=1, max_length=200)
    confirm: StrictBool = False
