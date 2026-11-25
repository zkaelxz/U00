// User administration and the audit log (api/routers/admin_users_routes.py).
// Reads need admin.users.read, writes admin.users. The writes are not
// local_only() routes, so they use plain postJson, not pcOnlyFetch: an admin
// may change member accounts from away, and the server refuses (403) any
// other change to an admin account there, and adding a person.
import type {
  AdminAddUserRequest, AdminDevice, AdminSessionsRevoked, AdminUser, AdminUserDeleted, AdminUserList, AuditPage, AuditQuery,
} from '../types/adminUsers'
import { deleteJson, getJson, postJson, type Fetch } from './client'

const BASE = '/api/admin'

// Server cap (services/auth_service.AUDIT_PAGE_MAX is 200).
const AUDIT_PAGE_SIZE = 50

export const listAdminUsers = (f?: Fetch) => getJson<AdminUserList>(`${BASE}/users`, f)

/** Allowlists an email with the household rights (main PC only). */
export const addAdminUser = (body: AdminAddUserRequest, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users`, body, f)

/** Deletes a deactivated member account for good. */
export const deleteAdminUser = (id: number, f?: Fetch) =>
  deleteJson<AdminUserDeleted>(`${BASE}/users/${id}`, f)

export const setUserActive = (id: number, active: boolean, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users/${id}/${active ? 'activate' : 'deactivate'}`, undefined, f)

export const revokeUserSessions = (id: number, f?: Fetch) =>
  postJson<AdminSessionsRevoked>(`${BASE}/users/${id}/revoke-sessions`, undefined, f)

/** One person's signed-in devices, most recently used first. */
export const listUserDevices = (id: number, f?: Fetch) =>
  getJson<{ devices: AdminDevice[] }>(`${BASE}/users/${id}/devices`, f)

export const revokeUserDevice = (id: number, sessionId: number, f?: Fetch) =>
  postJson<AdminSessionsRevoked>(`${BASE}/users/${id}/devices/${sessionId}/revoke`, undefined, f)

export const revokeUserAdmin = (id: number, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users/${id}/revoke-admin`, undefined, f)

export const setUserPermission = (id: number, permission: string, granted: boolean, f?: Fetch) => {
  const url = `${BASE}/users/${id}/permissions/${encodeURIComponent(permission)}`
  return granted ? postJson<AdminUser>(url, undefined, f) : deleteJson<AdminUser>(url, f)
}

/** Sets or clears (empty) a person's optional name; allowed on any account, from any device. */
export const setUserDisplayName = (id: number, displayName: string, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users/${id}/display-name`, { display_name: displayName }, f)

/** A member's own monthly limit for paid engines, in USD; null clears it. */
export const setUserPaidLimit = (id: number, monthlyCapUsd: number | null, f?: Fetch) =>
  postJson<AdminUser>(`${BASE}/users/${id}/paid-limit`, { monthly_cap_usd: monthlyCapUsd }, f)

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
