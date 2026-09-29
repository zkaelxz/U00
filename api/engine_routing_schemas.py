"""
api/engine_routing_schemas.py -- request/response models for Step 36's
"Which engine does what" routes (api/routers/engine_routing_routes.py).
Kept out of api/schemas.py so this step could be built alongside another
branch editing that file. No key, URL or path field exists on any model.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CapabilityRoute(BaseModel):
    id: str
    label: str
    help: str
    requires: str
    engine: str
    default_engine: str
    is_default: bool
    engine_supported: bool
    choices: List[str]


class EngineTestOutcome(BaseModel):
    ok: bool
    tested_at: str
    error: Optional[str] = None


class EngineRouteStatus(BaseModel):
    engine: str
    tags: List[str]
    needs_key: bool
    key_configured: bool
    status: str  # not_configured | untested | working | failed
    last_test: Optional[EngineTestOutcome] = None


class EngineRouting(BaseModel):
    capabilities: List[CapabilityRoute]
    engines: List[EngineRouteStatus]


class CapabilityEngineRequest(BaseModel):
    """engine null resets the capability to its default."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)


class EngineTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Optional[str] = Field(None, max_length=120)
