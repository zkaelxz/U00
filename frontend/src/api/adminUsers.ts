// User administration and the audit log (api/routers/admin_users_routes.py).
// Every route needs admin.users; the writes are not PC-only (an admin can
// lock out an account from anywhere), so they use plain postJson, not
// pcOnlyFetch: a 403 here means "not an admin", not "not at the PC".
import type {
  AdminSessionsRevoked, AdminUser, AdminUserList, AuditPage, AuditQuery,
} from '../types/adminUsers'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

const BASE = '/api/admin'

// Server cap (services/auth_service.AUDIT_PAGE_MAX is 200).
export const AUDIT_PAGE_SIZE = 50

export const listAdminUsers = (f?: Fetch) => getJson<AdminUserList>(`${BASE}/users`, f)

export const setUserActive = (id: number, active: boolean, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users/${id}/${active ? 'activate' : 'deactivate'}`, undefined, f)

export const revokeUserSessions = (id: number, f?: Fetch) =>
  postJson<AdminSessionsRevoked>(`${BASE}/users/${id}/revoke-sessions`, undefined, f)

/** "?limit=50&action=..." with empty filters left out. */
export function auditQuery(q: AuditQuery = {}): string {
  const p = new URLSearchParams()
  p.set('limit', String(q.limit ?? AUDIT_PAGE_SIZE))
  if (q.before_id != null) p.set('before_id', String(q.before_id))
  if (q.action) p.set('action', q.action)
  if (q.user_id != null) p.set('user_id', String(q.user_id))
  return `?${p.toString()}`
}

export const listAudit = (q: AuditQuery = {}, f?: Fetch) =>
  getJson<AuditPage>(`${BASE}/audit${auditQuery(q)}`, f)
