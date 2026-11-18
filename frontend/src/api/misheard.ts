import type { MisheardApplyResult, MisheardKeepResult, MisheardKeptCleared, MisheardList } from '../types/misheard'
import { deleteJson, getJson, postJson, type Fetch } from './client'

const base = (id: number) => `/api/misheard/dramas/${id}`

export const listMisheard = (id: number, f?: Fetch) => getJson<MisheardList>(base(id), f)

export const applyMisheard = (
  id: number,
  body: { heard: string; suggested: string; line_id?: number; expected_zh?: string },
  f?: Fetch,
) => postJson<MisheardApplyResult>(`${base(id)}/apply`, body, f)

export const keepMisheard = (id: number, body: { heard: string; suggested: string }, f?: Fetch) =>
  postJson<MisheardKeepResult>(`${base(id)}/keep`, body, f)

export const showKeptMisheard = (id: number, f?: Fetch) => deleteJson<MisheardKeptCleared>(`${base(id)}/keep`, f)
