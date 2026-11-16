"""api/schemas/resource_alerts.py -- the PC alerts card: each alert's rule, its allowed ranges and its defaults.

Numbers and booleans only; no path, host or secret.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, StrictBool

__all__ = ["ResourceAlertRule", "ResourceAlertRules", "ResourceAlertChange", "ResourceAlertsRequest"]


class ResourceAlertRule(BaseModel):
    id: str
    label: str
    unit: str
    has_limit: bool
    below: bool
    enabled: bool
    limit: Optional[float] = None
    minutes: int
    limit_min: Optional[float] = None
    limit_max: Optional[float] = None
    minutes_min: int
    minutes_max: int
    default_enabled: bool
    default_limit: Optional[float] = None
    default_minutes: int


class ResourceAlertRules(BaseModel):
    rules: List[ResourceAlertRule]


class ResourceAlertChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[StrictBool] = None
    limit: Optional[float] = None
    minutes: Optional[int] = None


class ResourceAlertsRequest(BaseModel):
    """Only the alerts named change; within one, only the fields sent."""
    model_config = ConfigDict(extra="forbid")
    rules: Dict[str, ResourceAlertChange]
