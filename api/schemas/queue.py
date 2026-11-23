"""api/schemas/queue.py -- /api/queue: the user-managed job queue on the Jobs
page. Items carry a title, a step and a status, plus the few settings of that
step changed for that one item (a whitelist; no path or URL goes in or
out). Everything else comes from the title's saved choices.
"""

from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from api.schemas.common import QueueSettingRow

__all__ = ["QueueItem", "QueueSchedule", "QueueState", "QueueAddItem", "QueueAddAllSteps", "QueueAddBulk", "QueueBulkAdded", "QueueBulkSkip", "QueueMove",
           "QueueScheduleSet", "QueuePolicy", "QueueSettingOption",
           "QueueSettingField", "QueueStepSettings", "TitleFlowRun", "NovelFlowStart", "FileRunStart"]

_QueueStep = Literal["transcribe", "translate", "export"]
# The download step exists only in the chain Quick video translate adds, and names (the glossary scan),
# import (a novel link), text and lines only in a title page's one-button run, and follow and run_translate
# only in the Add box's run; all are shown, never picked.
_QueueItemStep = Literal["download", "transcribe", "translate", "export", "names", "import", "text", "lines",
                         "follow", "run_translate"]


class QueueItem(BaseModel):
    id: int
    drama_id: int
    title: str
    step: _QueueItemStep
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
    overlap_online_translate: bool
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
    # This item's own changes to the title's saved settings; the keys are its step's.
    settings: Optional[Dict[str, Union[bool, int, str]]] = None


class QueueAddAllSteps(BaseModel):
    drama_id: int = Field(ge=1, lt=2**63)


class QueueAddBulk(BaseModel):
    drama_ids: List[Annotated[int, Field(ge=1, lt=2**63)]] = Field(min_length=1, max_length=200)
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
    # Only what is sent changes.
    stop_on_failure: Optional[bool] = None
    overlap_online_translate: Optional[bool] = None


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


class TitleFlowRun(BaseModel):
    """How far a title's one-button run got, audio and video or a novel's link (services/page_run_service.py)."""
    # done: the Add box's run translated (the other runs end at review instead).
    state: Literal["idle", "waiting", "running", "stopped", "review", "done"]
    step: Optional[_QueueItemStep] = None
    reason: Optional[str] = None
    # The step's job while it waits or runs, so a page can show its progress and cancel it.
    job_id: Optional[str] = None


class NovelFlowStart(BaseModel):
    """POST /api/queue/novel-flow/{id}. `url` is read only when no chapter is saved yet; the rest
    is what the lines step makes the lines with, as the chunk-and-tag run takes it."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(default="", max_length=2000)
    engine: Optional[StrictStr] = Field(default=None, max_length=40)
    model: Optional[StrictStr] = Field(default=None, max_length=200)
    chapter_from: Optional[int] = Field(default=None, ge=1)
    chapter_to: Optional[int] = Field(default=None, ge=1)
    one_narrator: bool = False
    # The page showed the estimate for a paid engine with no monthly cap and the person accepted it.
    cost_accepted: bool = False


class FileRunStart(BaseModel):
    """POST /api/queue/file-run/{id}: the job the upload started, and the box's engine and English variant."""
    model_config = ConfigDict(extra="forbid")
    job_id: StrictStr = Field(min_length=1, max_length=100)
    engine: StrictStr = Field(min_length=1, max_length=40)
    locale: Optional[StrictStr] = Field(default=None, max_length=10)
