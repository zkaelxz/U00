"""api/schemas/queue.py -- /api/queue: the user-managed job queue on the Jobs
page. Items carry a title, a step and a status only; no path, URL or option
payload goes in or out (options come from the title's saved choices).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

__all__ = ["QueueItem", "QueueSchedule", "QueueState", "QueueAddItem", "QueueMove",
           "QueueScheduleSet", "QueuePolicy"]

QueueStep = Literal["transcribe", "translate", "export"]


class QueueItem(BaseModel):
    id: int
    drama_id: int
    title: str
    step: QueueStep
    status: Literal["waiting", "running", "done", "failed", "skipped", "interrupted", "cancelled"]
    reason: Optional[str] = None
    job_id: Optional[str] = None
    # Translate items: the engine the title will use, and whether it is paid.
    engine: Optional[str] = None
    paid: bool = False
    # The caller may reorder, remove or retry it.
    mine: bool
    added_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class QueueSchedule(BaseModel):
    time: str            # "HH:MM", the PC's clock
    start_at: float      # epoch seconds of the next time it fires
    missed: bool         # passed while Baihe was off; never run on its own


class QueueState(BaseModel):
    version: int
    state: Literal["idle", "running", "paused"]
    stop_on_failure: bool
    schedule: Optional[QueueSchedule] = None
    notice: Optional[str] = None
    max_items: int
    # Start, pause, schedule and the policy are the PC owner's.
    can_control: bool
    monthly_cap_usd: float
    items: List[QueueItem]


class QueueAddItem(BaseModel):
    drama_id: int = Field(ge=1, lt=2**63)
    step: QueueStep


class QueueMove(BaseModel):
    direction: Literal["up", "down"]


class QueueScheduleSet(BaseModel):
    # None clears the schedule, a missed one too.
    time: Optional[str] = Field(None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class QueuePolicy(BaseModel):
    stop_on_failure: bool
