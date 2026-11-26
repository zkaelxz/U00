import type { CastLink, CastStatus, ShareDays } from '../types/cast'
import { deleteJson, getJson, postJson, type Fetch } from './client'

export const getCastStatus = (dramaId: number, f?: Fetch) => getJson<CastStatus>(`/api/cast/dramas/${dramaId}`, f)
// A link a TV can play the title's English-subtitled copy from, good for a few hours.
export const makeCastLink = (dramaId: number, f?: Fetch) =>
  postJson<CastLink>(`/api/cast/dramas/${dramaId}/link`, undefined, f)
// A link to send a person, good for 1, 3 or 7 days; it plays this one title's subtitled copy and nothing else.
export const makeShareLink = (dramaId: number, days: ShareDays, f?: Fetch) =>
  postJson<CastLink>(`/api/cast/dramas/${dramaId}/share`, { days }, f)
export const endShareLinks = (dramaId: number, f?: Fetch) =>
  deleteJson<{ ended: number }>(`/api/cast/dramas/${dramaId}/share`, f)
