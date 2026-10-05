// Re-cost of past spend estimates (api/routers/usage_recost_routes.py).
// Types mirror UsageRecost* in api/schemas/system.py.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export interface UsageRecostModelRow {
  model: string
  rows: number
  stored_usd: number
  recomputed_usd: number
}

export interface UsageRecostPreview {
  rows: number
  models: UsageRecostModelRow[]
  stored_usd: number
  recomputed_usd: number
  difference_usd: number
  month_stored_usd: number
  month_recomputed_usd: number
  recosted_rows: number
}

export interface UsageRecostResult {
  rows: number
  month_spend_usd: number
}

const BASE = '/api/settings/usage-recost'

export const getUsageRecost = (f?: Fetch) => getJson<UsageRecostPreview>(BASE, f)

// PC only: a 403 marks the tab remote.
export const applyUsageRecost = (f?: Fetch) =>
  postJson<UsageRecostResult>(`${BASE}/apply`, { confirm: true }, pcOnlyFetch(f))

export const undoUsageRecost = (f?: Fetch) => postJson<UsageRecostResult>(`${BASE}/undo`, {}, pcOnlyFetch(f))

export const usd = (n: number) => `$${n.toFixed(2)}`

// The plain sentence above the table. "About": cache-write tokens are not
// stored, so the new figure is an estimate.
export function recostSummary(p: UsageRecostPreview): string {
  if (p.rows === 0) return 'Nothing to change: no past usage was priced at a higher model’s rate.'
  return `${p.rows} past ${p.rows === 1 ? 'entry was' : 'entries were'} priced too high. Total logged: ${usd(p.stored_usd)}; about ${usd(p.recomputed_usd)} when re-costed (${usd(p.difference_usd)} less). This month would go from ${usd(p.month_stored_usd)} to about ${usd(p.month_recomputed_usd)}.`
}
