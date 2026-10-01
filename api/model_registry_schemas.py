"""
api/model_registry_schemas.py -- request/response models for the model
deprecation / migration assistant (api/routers/model_registry_routes.py,
Step 40). Kept out of api/schemas.py; no key field exists on any model.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ModelStatusItem(BaseModel):
    engine: str
    model: str
    kind: str
    where: str
    preset_id: Optional[int] = None
    status: str
    message: str
    replacement: Optional[str] = None
    note: Optional[str] = None
    listed_by_provider: Optional[bool] = None
    severity: int
    can_switch: bool
    # Defaults and tiers only: the engine or tier key, the built-in model, whether
    # the user replaced it, and models they could choose instead.
    key: Optional[str] = None
    builtin_model: Optional[str] = None
    is_override: bool = False
    candidates: List[str] = Field(default_factory=list)


class EngineCheck(BaseModel):
    ok: bool
    model_count: int
    error: Optional[str] = None


class ModelStatus(BaseModel):
    items: List[ModelStatusItem]
    warnings: int
    checked_at: Optional[str] = None
    engines_checked: Dict[str, EngineCheck] = Field(default_factory=dict)
    registry_updated: Optional[str] = None
    # The opt-in to offer provider-listed models, and the ones it adds now per engine.
    offer_provider_models: bool = False
    extra_models: Dict[str, List[str]] = Field(default_factory=dict)


class PresetModelSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_model: str = Field(max_length=100)
    to_model: str = Field(max_length=100)
    confirm: bool = False


class PresetModelSwitchResult(BaseModel):
    preset_id: int
    engine: Optional[str] = None
    from_model: str
    to_model: str


class ModelOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["default", "tier"]
    key: str = Field(max_length=60)
    from_model: str = Field(max_length=100)
    to_model: str = Field(max_length=100)
    confirm: bool = False


class ModelOverrideClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["default", "tier"]
    key: str = Field(max_length=60)
    confirm: bool = False


class ModelOverrideResult(BaseModel):
    kind: str
    key: str
    engine: str
    model: Optional[str] = None
    from_model: Optional[str] = None
    to_model: Optional[str] = None
    builtin_model: Optional[str] = None
