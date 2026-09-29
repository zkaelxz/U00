"""
api/sources_extraction_schemas.py -- request/response models for the
pasted-URL extraction extras (Streamlit Sources parity SO09, SO06): the AI
fallback opt-in on the URL imports, the engine list and the comic import. Kept out of
api/schemas.py so this batch could be built alongside another branch
editing that file (precedent: api/comic_schemas.py); the shared
ErrorResponse and SourcesUrlImportRequest still live there. No model
carries a key, and no response carries one.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, StrictBool, StrictStr

from api.schemas import SourcesUrlImportRequest


class SourcesUrlImportAiRequest(SourcesUrlImportRequest):
    """POST /api/sources/url/import. `use_ai` turns on the LLM fallback
    (off by default; it is only asked when deterministic extraction comes
    back empty or ambiguous). `engine` omitted = the saved default engine."""
    use_ai: StrictBool = False
    engine: Optional[StrictStr] = Field(default=None, min_length=1, max_length=40)


class SourcesAiEngines(BaseModel):
    """Engine names the AI fallback can use, and the saved default when it
    is one of them. Names only: no key and no key status."""
    engines: List[str]
    default: Optional[str] = None


class SourcesComicUrlImportRequest(SourcesUrlImportAiRequest):
    """POST /api/sources/url/import-comic. The drama must be a manhua,
    manga or manhwa drama."""
