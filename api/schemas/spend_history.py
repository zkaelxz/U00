"""api/schemas/spend_history.py -- GET /api/settings/spend-history: paid-API
spend by month and what caused it. Titles, labels and numbers only.
"""

from typing import List, Optional

from pydantic import BaseModel

__all__ = ["SpendBreakdownRow", "SpendMonthRow", "SpendHistory"]


class SpendBreakdownRow(BaseModel):
    # Raw operation name for the operation breakdown; null elsewhere and on "Other".
    key: Optional[str] = None
    label: str
    is_other: bool = False
    cost_usd: float
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int


class SpendMonthRow(BaseModel):
    month: str  # "YYYY-MM", UTC
    cost_usd: float
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    # True for the month a monthly-cap reset falls in: the cap counts only
    # rows since the reset, so cap_counted_usd can be lower than cost_usd.
    overlaps_reset: bool
    cap_counted_usd: Optional[float] = None


class SpendHistory(BaseModel):
    months: List[SpendMonthRow]
    selected_month: Optional[str] = None
    cap_reset_at: Optional[str] = None
    max_months: int
    by_operation: List[SpendBreakdownRow]
    by_engine_model: List[SpendBreakdownRow]
    by_title: List[SpendBreakdownRow]
