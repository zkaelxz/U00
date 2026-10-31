"""api/schemas/bug_report.py -- Report-a-problem shapes (services/bug_report_service.py).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "BugReportRouteVisit",
    "BugReportConsoleEntry",
    "BugReportErrorEntry",
    "BugReportFailedRequest",
    "BugReportViewport",
    "BugReportClient",
    "BugReportSaved",
    "BugReportText",
    "BugReportDeleteConfirm",
    "BugReportListItem",
    "BugReportDeleted",
]


# ---------------------------------------------------------------------------
# Report a problem (services/bug_report_service.py): the React header's
# "Report a problem" dialog. The report is sent as the multipart field
# `report` (JSON matching BugReportClient, max 256 KB) plus an optional
# `screenshot` file (PNG/JPEG, max 5 MB). Client buffers carry no request
# or response bodies, headers, cookies or line text.
# ---------------------------------------------------------------------------
class BugReportRouteVisit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: str = Field(max_length=300)
    at: Optional[str] = Field(None, max_length=40)


class BugReportConsoleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["error", "warn"]
    message: str = Field(max_length=2000)
    at: Optional[str] = Field(None, max_length=40)


class BugReportErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["error", "unhandledrejection"]
    message: str = Field(max_length=2000)
    source: Optional[str] = Field(None, max_length=500)
    at: Optional[str] = Field(None, max_length=40)


class BugReportFailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(max_length=10)
    path: str = Field(max_length=500)
    status: int = Field(ge=0, le=999)
    code: Optional[str] = Field(None, max_length=80)
    at: Optional[str] = Field(None, max_length=40)


class BugReportViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=0, le=100000)
    height: int = Field(ge=0, le=100000)
    dpr: Optional[float] = Field(None, ge=0, le=20)


class BugReportClient(BaseModel):
    """What the browser sends. Lists are the capture module's ring buffers."""
    model_config = ConfigDict(extra="forbid")
    what_happened: str = Field(min_length=1, max_length=5000)
    expected: str = Field("", max_length=5000)
    include_server_log: StrictBool = True
    route: str = Field("", max_length=300)
    route_history: List[BugReportRouteVisit] = Field(default_factory=list, max_length=10)
    console: List[BugReportConsoleEntry] = Field(default_factory=list, max_length=30)
    errors: List[BugReportErrorEntry] = Field(default_factory=list, max_length=30)
    failed_requests: List[BugReportFailedRequest] = Field(default_factory=list, max_length=30)
    app_version: str = Field("", max_length=60)
    api_version: str = Field("", max_length=60)
    environment: str = Field("", max_length=60)
    build_id: str = Field("", max_length=120)
    user_agent: str = Field("", max_length=500)
    viewport: Optional[BugReportViewport] = None
    mode: Literal["pc", "lan", "remote", "unknown"] = "unknown"


class BugReportSaved(BaseModel):
    """`markdown` (for Copy report) includes the server section (commit,
    setup, log tail) only for a caller holding admin.diagnostics; it is
    always saved on the PC. `issue_markdown`, `what_happened`, `expected`
    and `title` are the server-scrubbed texts for the public GitHub issue
    link, which never carries the server section."""
    id: int
    stamp: str
    markdown: str
    issue_markdown: str
    what_happened: str
    expected: str
    title: str


class BugReportText(BaseModel):
    """One saved report's markdown (with the server section)."""
    id: int
    stamp: str
    markdown: str


class BugReportDeleteConfirm(BaseModel):
    """PC-only delete: `stamp` is the folder stamp from the list, so a stale
    list can't delete a different report."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    stamp: str = Field(pattern=r"^\d{8}T\d{6}Z$")


class BugReportListItem(BaseModel):
    id: int
    stamp: str
    created_at: Optional[str] = None
    summary: str
    route: Optional[str] = None
    mode: Optional[str] = None
    has_screenshot: bool
    has_server_log: bool


class BugReportDeleted(BaseModel):
    id: int
    deleted: bool
