"""api/schemas/account_prefs.py -- the preferences an account keeps on the server, and a member's own limits."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class AccountPreferences(BaseModel):
    """Each field is absent until the person has chosen one on some device."""
    model_config = ConfigDict(extra="forbid")
    theme: Optional[Literal["system", "light", "dark", "sepia", "oled"]] = None
    live_focus: Optional[bool] = None



class MemberLimits(BaseModel):
    """A member's own limits and how much of each is used; a limit is None when none is set."""
    daily_job_limit: Optional[int] = None
    jobs_today: int
    monthly_paid_cap_usd: Optional[float] = None
    month_paid_spend_usd: float
    title_limit: int
    titles_owned: int


__all__ = ["AccountPreferences", "MemberLimits"]
