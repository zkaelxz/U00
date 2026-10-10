"""
api/web_search_schemas.py -- request/response models for the optional
web-search fallback (api/routers/web_search_routes.py).
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class WebSearchStatus(BaseModel):
    """Whether the fallback can be used; the server address is not shown."""
    enabled: bool


class WebSearchConfig(BaseModel):
    enabled: bool
    base_url: Optional[str] = None


class WebSearchConfigUpdate(BaseModel):
    """Omitted fields keep their saved value; "" clears the address."""
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    base_url: Optional[str] = Field(None, max_length=2000)
    # Changing the address decides where the app sends requests: needs
    # confirm and the key-write gate, like the engine endpoint URLs.
    confirm: bool = False


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)


class WebSearchResult(BaseModel):
    title: str
    snippet: str
    url: str
    domain: str


class WebSearchResults(BaseModel):
    query: str
    source: Literal["searxng"]
    results: List[WebSearchResult]


class WebSearchTestResult(BaseModel):
    ok: bool
    result_count: int
