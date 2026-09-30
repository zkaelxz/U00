// Mirrors api/admin_users_schemas.py (user administration + audit log view).

export interface AdminUser {
  id: number
  email: string
  display_name: string
  is_admin: boolean
  is_active: boolean
  has_google_binding: boolean
  created_at: string | null
  permissions: string[]
  // Live (unexpired) sign-ins; no session details are ever sent.
  active_sessions: number
  // The row is the signed-in caller (never true for the local owner).
  is_self: boolean
}

export interface AdminUserList {
  users: AdminUser[]
}

export interface AdminSessionsRevoked {
  user_id: number
  revoked: number
}

export interface AuditEvent {
  id: number
  ts: string
  // null: the PC (local owner, CLI) or the system (e.g. a refused sign-in).
  user_id: number | null
  action: string
  detail: string
}

export interface AuditPage {
  events: AuditEvent[]
  // Pass as before_id for the next (older) page; null at the end.
  next_before_id: number | null
  actions: string[]
}

export interface AuditQuery {
  limit?: number
  before_id?: number | null
  action?: string | null
  user_id?: number | null
}
