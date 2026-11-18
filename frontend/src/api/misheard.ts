import type { MisheardApplyResult, MisheardList, MisheardRememberResult } from '../types/misheard'
import { getJson, postJson, type Fetch } from './client'

const base = (id: number) => `/api/misheard/dramas/${id}`

export const listMisheard = (id: number, f?: Fetch) => getJson<MisheardList>(base(id), f)

export const applyMisheard = (
  id: number,
  body: { heard: string; suggested: string; line_id?: number; expected_zh?: string },
  f?: Fetch,
) => postJson<MisheardApplyResult>(`${base(id)}/apply`, body, f)

export const rememberMisheard = (id: number, body: { heard: string; term_id: number }, f?: Fetch) =>
  postJson<MisheardRememberResult>(`${base(id)}/remember`, body, f)
