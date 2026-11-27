"""api/schemas/account_prefs.py -- the preferences an account keeps on the server."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class AccountPreferences(BaseModel):
    """Each field is absent until the person has chosen one on some device."""
    model_config = ConfigDict(extra="forbid")
    theme: Optional[Literal["system", "light", "dark", "sepia", "oled"]] = None
    live_focus: Optional[bool] = None

__all__ = ["AccountPreferences"]
