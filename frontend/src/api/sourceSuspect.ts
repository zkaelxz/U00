import type { SourceSuspectCheckResult, SourceSuspectList } from '../types/sourceSuspect'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const base = (id: number) => `/api/source-suspect/dramas/${id}`

export const listSourceSuspects = (id: number, f?: Fetch) => getJson<SourceSuspectList>(base(id), f)

export const checkSourceText = (id: number, f?: Fetch) =>
  postJson<SourceSuspectCheckResult>(`${base(id)}/check`, undefined, f)
