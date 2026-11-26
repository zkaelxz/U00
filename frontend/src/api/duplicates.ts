// Duplicate titles (api/routers/drama_routes.py: GET /api/dramas/duplicates and POST .../scan).
import { getJson, postJson, type Fetch } from './client'

export type DuplicateKind = 'file' | 'link' | 'name'
export type DuplicateGroup = { same: DuplicateKind; items: { id: number; title: string }[] }
export type DuplicateGroups = { groups: DuplicateGroup[]; unhashed: number }

export const DUPLICATE_SCAN_JOB_ID = 'duplicate_scan'

export const getDuplicateTitles = (f?: Fetch) => getJson<DuplicateGroups>('/api/dramas/duplicates', f)

export const startDuplicateScan = (f?: Fetch) => postJson<{ job_id: string }>('/api/dramas/duplicates/scan', {}, f)
