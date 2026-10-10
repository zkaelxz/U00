import type { TimingCheckStatus, TimingSnapResult } from '../types/timingCheck'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const base = (id: number) => `/api/timing-check/dramas/${id}`

export const getTimingCheck = (id: number, f?: Fetch) => getJson<TimingCheckStatus>(base(id), f)

export const startTimingCheck = (id: number, f?: Fetch) =>
  postJson<{ job_id: string }>(`${base(id)}/run`, undefined, f)

// lineIds omitted = every flagged line with a suggestion.
export const snapToSpeech = (id: number, lineIds?: number[], f?: Fetch) =>
  postJson<TimingSnapResult>(`${base(id)}/snap`, lineIds ? { line_ids: lineIds } : {}, f)
