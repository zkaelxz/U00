"""Schemas for Diagnostics > "Resources" (GET /api/diagnostics/resources): the resource monitor's last readings and each alert rule's standing. Numbers, booleans and fixed labels only."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

__all__ = ["ResourceMetric", "ResourceReadings"]


class ResourceMetric(BaseModel):
    name: Literal["gpu_temp", "gpu_throttle", "ram", "disk_free", "gpu_memory_warn", "gpu_memory", "cpu"]
    label: str
    unit: str = Field(description="°C, %, GB, or empty for GPU throttling (1 = cutting clocks, 0 = not).")
    low: bool = Field(description="True when the alert is for a value under the limit (free disk space).")
    enabled: bool
    limit: Optional[float] = Field(None, description="None for GPU throttling, which has no number.")
    minutes: int = Field(description="How long the value must stay past the limit before an alert.")
    value: Optional[float] = Field(None, description="The last reading; None when this PC cannot read it.")
    alerting: bool
    past_seconds: Optional[int] = Field(
        None, description="How long the value has been past the limit, or None when it is not.")


class ResourceReadings(BaseModel):
    sampled_seconds_ago: Optional[int] = Field(
        None, description="None when the monitor has not sampled yet (it may be off with background services).")
    sample_seconds: int = Field(description="How often the monitor samples.")
    metrics: List[ResourceMetric]
