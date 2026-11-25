import type { CastLink, CastStatus } from '../types/cast'
import { getJson, postJson, type Fetch } from './client'

export const getCastStatus = (dramaId: number, f?: Fetch) => getJson<CastStatus>(`/api/cast/dramas/${dramaId}`, f)
// A link a TV can play the title's English-subtitled copy from, good for a few hours.
export const makeCastLink = (dramaId: number, f?: Fetch) =>
  postJson<CastLink>(`/api/cast/dramas/${dramaId}/link`, undefined, f)
