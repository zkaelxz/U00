// Pure helpers for the Users and Audit log sections of Diagnostics. The
// server enforces every rule below (409); these only explain up front why
// a button is off.
import type { SessionState } from '../../hooks/useSession'
import type { AdminUser, AuditEvent } from '../../types/adminUsers'

/** Show the sections? Only to a caller holding admin.users. If /me is
 * unavailable the page renders as before sign-in existed (routes still enforce). */
export function canManageUsers(s: SessionState): boolean {
  if (s.status === 'loading') return false
  if (s.status === 'unavailable') return true
  return s.me.permissions.includes('admin.users')
}

export function activeAdminCount(users: AdminUser[]): number {
  return users.filter((u) => u.is_admin && u.is_active).length
}

/** Why this user can't be deactivated now, or null if they can. */
export function deactivateBlock(user: AdminUser, users: AdminUser[]): string | null {
  if (!user.is_active) return null
  if (user.is_self) return "You can't deactivate your own account."
  if (user.is_admin && activeAdminCount(users) <= 1) {
    return "The last active admin can't be deactivated."
  }
  return null
}

/** Why this user's sessions can't be ended now, or null if they can. */
export function revokeBlock(user: AdminUser): string | null {
  if (user.is_self) return 'Use Sign out to end your own session.'
  if (user.active_sessions === 0) return 'Not signed in anywhere.'
  return null
}

export function userName(user: AdminUser): string {
  return user.display_name ? `${user.display_name} (${user.email})` : user.email
}

export function sessionsText(n: number): string {
  return n === 0 ? 'not signed in' : n === 1 ? 'signed in on 1 device' : `signed in on ${n} devices`
}

/** Who did it: the user's name, "User #id" for an unknown id, "PC or system" for none. */
export function auditActor(userId: number | null, users: AdminUser[] | null): string {
  if (userId == null) return 'PC or system'
  const u = users?.find((x) => x.id === userId)
  return u ? userName(u) : `User #${userId}`
}

const ACTIONS: Record<string, string> = {
  'login.success': 'Signed in',
  'login.denied': 'Sign-in refused',
  'login.error': 'Sign-in failed',
  'login.rate_limited': 'Sign-in rate limited',
  logout: 'Signed out',
  'session.create': 'Session started',
  'session.revoke': 'Session ended',
  'session.revoke_all': 'All sessions ended',
  'user.add': 'User added',
  'user.activate': 'User activated',
  'user.deactivate': 'User deactivated',
  'user.grant_admin_local': 'Admin granted on the PC',
  'user.bind_google': 'Google account linked',
  'permission.grant': 'Permission granted',
  'permission.revoke': 'Permission removed',
  'library.restore': 'Library restored',
  'library.restore_drama': 'Title restored from backup',
}

/** A plain label for an audit action; unknown ones are shown as stored. */
export function auditActionLabel(action: string): string {
  return ACTIONS[action] ?? action
}

/** "2026-09-30T12:34:56Z" -> "2026-09-30 12:34 UTC"; anything else as stored. */
export function auditTime(ts: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?Z$/.exec(ts)
  return m ? `${m[1]} ${m[2]} UTC` : ts
}

/** Append an older page, skipping any row already shown. */
export function appendAudit(cur: AuditEvent[], older: AuditEvent[]): AuditEvent[] {
  const seen = new Set(cur.map((e) => e.id))
  return [...cur, ...older.filter((e) => !seen.has(e.id))]
}
