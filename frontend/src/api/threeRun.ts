// "Run 3 times, review differences" (/api/three-run/...): the review's state, the
// per-line picks and the undo snapshot. The runs themselves start through the normal Translate run.
import type { ThreeRunBegun, ThreeRunPick, ThreeRunPickResult, ThreeRunState } from '../types/threeRun'
import { deleteJson, getJson, postJson, type Fetch } from './client'

const base = (id: number) => `/api/three-run/dramas/${id}`

export const getThreeRun = (id: number, f?: Fetch) => getJson<ThreeRunState>(base(id), f)

export const beginThreeRun = (id: number, lineIds: number[] | null, f?: Fetch) =>
  postJson<ThreeRunBegun>(`${base(id)}/begin`, lineIds ? { line_ids: lineIds } : {}, f)

export const captureThreeRun = (id: number, run: number, f?: Fetch) =>
  postJson<ThreeRunState>(`${base(id)}/capture`, { run }, f)

export const pickThreeRun = (id: number, picks: ThreeRunPick[], f?: Fetch) =>
  postJson<ThreeRunPickResult>(`${base(id)}/pick`, { picks }, f)

export const keepRestThreeRun = (id: number, run: number, f?: Fetch) =>
  postJson<ThreeRunPickResult>(`${base(id)}/keep-rest`, { run }, f)

export const endThreeRun = (id: number, f?: Fetch) => deleteJson<void>(base(id), f)
