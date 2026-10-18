"""api/schemas/queue.py -- /api/queue: the user-managed job queue on the Jobs
page. Items carry a title, a step and a status, plus, for Transcribe, the few
settings changed for that one item (a whitelist; no path or URL goes in or
out). Everything else comes from the title's saved choices.
"""

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

__all__ = ["QueueItem", "QueueSchedule", "QueueState", "QueueAddItem", "QueueAddAllSteps", "QueueAddBulk", "QueueBulkAdded", "QueueBulkSkip", "QueueMove",
           "QueueScheduleSet", "QueuePolicy", "QueueSettingRow", "QueueSettingOption",
           "QueueSettingField", "QueueStepSettings"]

_QueueStep = Literal["transcribe", "translate", "export"]


class QueueItem(BaseModel):
    id: int
    drama_id: int
    title: str
    step: _QueueStep
    status: Literal["waiting", "running", "done", "failed", "skipped", "interrupted", "cancelled"]
    reason: Optional[str] = None
    job_id: Optional[str] = None
    # Translate items: the engine the title will use, and whether it is paid.
    engine: Optional[str] = None
    paid: bool = False
    # Transcribe items: what this item changes from the title's saved options.
    changed_settings: List["QueueSettingRow"] = []
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
    step: _QueueStep
    # Transcribe only: this item's own changes to the title's saved settings.
    settings: Optional[Dict[str, Union[bool, int, str]]] = None


class QueueAddAllSteps(BaseModel):
    drama_id: int = Field(ge=1, lt=2**63)


class QueueAddBulk(BaseModel):
    drama_ids: List[int] = Field(min_length=1, max_length=500)
    step: Literal["transcribe", "translate", "export", "all"]


class QueueBulkSkip(BaseModel):
    drama_id: int
    reason: str


class QueueBulkAdded(BaseModel):
    queue: "QueueState"
    added: List[int]
    skipped: List[QueueBulkSkip]


class QueueMove(BaseModel):
    direction: Literal["up", "down"]


class QueueScheduleSet(BaseModel):
    # None clears the schedule, a missed one too.
    time: Optional[str] = Field(None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class QueuePolicy(BaseModel):
    stop_on_failure: bool


class QueueSettingRow(BaseModel):
    label: str
    value: str
    # Set on the engine row: its id, which the page words.
    engine: Optional[str] = None


class QueueSettingOption(BaseModel):
    value: str
    label: str


class QueueSettingField(BaseModel):
    """One setting a queued item may change, starting at the title's saved value."""
    key: str
    label: str
    kind: Literal["select", "number", "toggle"]
    value: Union[bool, int, str]
    options: List[QueueSettingOption] = []
    min: Optional[int] = None
    max: Optional[int] = None
    # Why the caller can't change it (a paid option without permission).
    locked: Optional[str] = None


class QueueStepSettings(BaseModel):
    drama_id: int
    step: _QueueStep
    title: str
    rows: List[QueueSettingRow]
    fields: List[QueueSettingField] = []
