// Who can see each drama and series (api/routers/sharing_routes.py).
// The item list and a new owner are admins only (checked by the server, from
// away too); a flip is lines.edit and the server allows only the item's owner
// or an admin; share-by-default is the caller's own setting.
import type { SetOwnerResult, SetPrivateResult, ShareByDefault, SharingKind, SharingList } from '../types/sharing'
import { getJson, postJson, type Fetch } from './client'

const BASE = '/api/sharing'

export const listSharing = (offset = 0, limit = 100, f?: Fetch) =>
  getJson<SharingList>(`${BASE}/items?offset=${offset}&limit=${limit}`, f)

export const setItemPrivate = (kind: SharingKind, id: number, isPrivate: boolean, f?: Fetch) =>
  postJson<SetPrivateResult>(`${BASE}/${kind === 'series' ? 'series' : 'dramas'}/${id}/private`, { private: isPrivate }, f)

/** `ownerUserId` null gives the item to the PC (no account owner). */
export const setItemOwner = (kind: SharingKind, id: number, ownerUserId: number | null, f?: Fetch) =>
  postJson<SetOwnerResult>(`${BASE}/${kind === 'series' ? 'series' : 'dramas'}/${id}/owner`, { owner_user_id: ownerUserId }, f)

export const getShareByDefault = (f?: Fetch) => getJson<ShareByDefault>(`${BASE}/share-by-default`, f)

export const setShareByDefault = (share: boolean, f?: Fetch) =>
  postJson<ShareByDefault>(`${BASE}/share-by-default`, { share_by_default: share }, f)
