"""
api/assistant_schemas.py -- request/response models for the maintenance
assistant routes (api/routers/assistant_routes.py, roadmap Step 42). Kept
out of api/schemas.py so this slice can be built alongside other branches
editing that file; the shared ErrorResponse still lives there. No model
carries a key, a token or an absolute path.
"""

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

BacklogKind = Literal["bug", "feature", "note"]
Scalar = Union[str, int, float, bool, None]


class AssistantSettings(BaseModel):
    developer_mode: bool
    engine: Optional[str] = None
    model: Optional[str] = None
    engine_choices: List[str]


class AssistantSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    developer_mode: Optional[bool] = None
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)


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


class AssistantAnswer(BaseModel):
    answer: str
    proposed_patches: List[AssistantPatch]
    suggested_backlog: List[AssistantBacklogSuggestion]
    tool_calls: List[AssistantToolCall]
    engine: str
    model: Optional[str] = None


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
