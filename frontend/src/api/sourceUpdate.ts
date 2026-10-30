// Update and restart for a git working copy (api/routers/source_update_routes.py). Unlike the
// release updates, an admin may use these from away: system.update, not PC only.
import type { SourceUpdateApplyResponse, SourceUpdateStatus } from '../types/update'
import { getJson, postJson, type Fetch } from './client'

const BASE = '/api/update/source'

export const getSourceUpdate = (f?: Fetch) => getJson<SourceUpdateStatus>(BASE, f)

export const checkSourceUpdate = (f?: Fetch) => postJson<SourceUpdateStatus>(`${BASE}/check`, {}, f)

export const applySourceUpdate = (f?: Fetch) =>
  postJson<SourceUpdateApplyResponse>(`${BASE}/apply`, { confirm: true }, f)
