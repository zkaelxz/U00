"""
api/assistant_schemas.py -- request/response models for the maintenance
assistant routes (api/routers/assistant_routes.py, roadmap Step 42). Kept
out of api/schemas.py so this slice can be built alongside other branches
editing that file; the shared ErrorResponse still lives there. No model
carries a key, a token or an absolute path.
"""

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool

BacklogKind = Literal["bug", "feature", "note"]
Scalar = Union[str, int, float, bool, None]


class AssistantSettings(BaseModel):
    developer_mode: bool
    engine: Optional[str] = None
    model: Optional[str] = None
    engine_choices: List[str]
    roles_enabled: bool = False
    review_engine: Optional[str] = None
    review_model: Optional[str] = None
    review_engine_choices: List[str] = Field(default_factory=list)
    default_engine: str = "ollama"
    local_engines: List[str] = Field(default_factory=list)
    cloud_consent: Dict[str, bool] = Field(default_factory=dict)


class AssistantSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    developer_mode: Optional[bool] = None
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)
    roles_enabled: Optional[bool] = None
    review_engine: Optional[str] = Field(None, max_length=40)
    review_model: Optional[str] = Field(None, max_length=100)
    cloud_consent: Optional[Dict[str, StrictBool]] = None


class AssistantTool(BaseModel):
    name: str
    description: str
    tier: Literal["green"]


class AssistantTools(BaseModel):
    tools: List[AssistantTool]
    write_tools: List[str]


class AssistantChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AssistantAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    chat_history: List[AssistantChatTurn] = Field(default_factory=list, max_length=20)
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)


class AssistantPatch(BaseModel):
    id: str
    patch: str
    files: List[str]


class AssistantBacklogSuggestion(BaseModel):
    kind: BacklogKind
    text: str


class AssistantToolCall(BaseModel):
    id: str
    name: str
    args: Dict[str, Scalar]
    ok: bool
    summary: str


class AssistantReview(BaseModel):
    """Step 60: the independent review role's view of a proposed fix."""
    engine: Optional[str] = None
    model: Optional[str] = None
    verdict: Literal["agrees", "concerns", "unclear", "unavailable"]
    notes: str
    tool_calls: List[AssistantToolCall]


class AssistantAnswer(BaseModel):
    answer: str
    proposed_patches: List[AssistantPatch]
    suggested_backlog: List[AssistantBacklogSuggestion]
    tool_calls: List[AssistantToolCall]
    engine: str
    model: Optional[str] = None
    review: Optional[AssistantReview] = None


class AssistantChangelogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_ref: str = Field(min_length=1, max_length=100)
    to_ref: str = Field("HEAD", min_length=1, max_length=100)
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)


class AssistantChangelog(BaseModel):
    changelog: str
    commit_count: int
    from_ref: str
    to_ref: str
    truncated: bool = False


class AssistantBacklogItem(BaseModel):
    id: int
    kind: BacklogKind
    text: str
    created_at: str


class AssistantBacklog(BaseModel):
    items: List[AssistantBacklogItem]


class AssistantBacklogAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: BacklogKind
    text: str = Field(min_length=1, max_length=1000)


class AssistantConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class AssistantBacklogDeleted(BaseModel):
    deleted: bool


class AssistantBacklogCleared(BaseModel):
    deleted: int


# --- Step 72: deliver a proposed fix as a GitHub pull request ---------------

class AssistantGithubStatus(BaseModel):
    """Never carries the token: only whether one is configured."""
    enabled: bool
    repo: Optional[str] = None
    base_branch: str
    token_configured: bool
    branch_prefix: str


class AssistantGithubSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    repo: Optional[str] = Field(None, max_length=141)
    base_branch: Optional[str] = Field(None, max_length=100)


class AssistantGithubTokenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=512)
    confirm: bool = False


class AssistantGithubTokenResult(BaseModel):
    token_configured: bool


class AssistantGithubConnection(BaseModel):
    ok: bool
    repo: str
    default_branch: Optional[str] = None
    can_push: bool
    base_branch: str
    base_exists: bool


class AssistantGithubPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: str = Field(min_length=1, max_length=200_000)
    title: str = Field(min_length=1, max_length=200)


class AssistantGithubFile(BaseModel):
    path: str
    change: Literal["add", "modify", "delete"]


class AssistantGithubPreview(BaseModel):
    repo: str
    base_branch: str
    branch_prefix: str
    title: str
    files: List[AssistantGithubFile]
    patch: str
    sha256: str


class AssistantGithubDeliverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: str = Field(min_length=1, max_length=200_000)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field("", max_length=20_000)
    sha256: str = Field(min_length=64, max_length=64)
    confirm: bool = False


class AssistantGithubDelivered(BaseModel):
    pr_url: str
    pr_number: Optional[int] = None
    branch: str
    base_branch: str
    repo: str
    files: List[AssistantGithubFile]
