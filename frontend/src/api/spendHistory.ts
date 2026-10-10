// Spend by month (api/routers/spend_history_routes.py).
// Types mirror SpendHistory in api/schemas/spend_history.py.
import { getJson } from './client'

type Fetch = typeof fetch

export interface SpendBreakdownRow {
  key: string | null
  label: string
  is_other: boolean
  cost_usd: number
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
}

export interface SpendMonthRow {
  month: string
  cost_usd: number
  calls: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  overlaps_reset: boolean
  cap_counted_usd: number | null
}

export interface SpendHistory {
  months: SpendMonthRow[]
  selected_month: string | null
  cap_reset_at: string | null
  max_months: number
  by_operation: SpendBreakdownRow[]
  by_engine_model: SpendBreakdownRow[]
  by_title: SpendBreakdownRow[]
}

const BASE = '/api/settings/spend-history'

export const SPEND_HISTORY_CSV = `${BASE}/export.csv`

export const getSpendHistory = (month?: string, f?: Fetch) =>
  getJson<SpendHistory>(month ? `${BASE}?month=${encodeURIComponent(month)}` : BASE, f)

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2026-10" -> "Oct 2026". Pure string work, so the viewer's time zone never moves a UTC month. */
export function monthLabel(month: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month)
  return m ? `${MONTH_NAMES[Number(m[2]) - 1] ?? m[2]} ${m[1]}` : month
}

/** Small amounts keep their cents-and-below detail so a $0.004 call is not shown as $0.00. */
export const money = (n: number) => `$${n.toFixed(n > 0 && n < 0.01 ? 4 : 2)}`
