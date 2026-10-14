// A signed-in person's own Ollama key (api/routers/member_key_routes.py). The
// server takes the user from the session, so these only ever reach the
// caller's own key; no response carries the key itself. An admin removes
// someone's key from People (admin_users_routes).
import type { MemberKeyRemoved, MemberKeyStatus } from '../types/memberKey'
import { deleteJson, getJson, postJson } from './client'

type Fetch = typeof fetch

const OWN = '/api/auth/ollama-key'

export const getMyOllamaKey = (f?: Fetch) => getJson<MemberKeyStatus>(OWN, f)

export const saveMyOllamaKey = (key: string, f?: Fetch) => postJson<MemberKeyStatus>(OWN, { key }, f)

export const removeMyOllamaKey = (f?: Fetch) => deleteJson<MemberKeyStatus>(OWN, f)

export const removeUserOllamaKey = (userId: number, f?: Fetch) =>
  deleteJson<MemberKeyRemoved>(`/api/admin/users/${userId}/ollama-key`, f)
