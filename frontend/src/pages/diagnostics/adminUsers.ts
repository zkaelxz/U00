// Pure helpers for the Users and Audit log sections of Diagnostics. The
// server enforces every rule below (409, or 403 for an admin target off the
// PC); these only explain up front why a button is off.
import type { PcMode } from '../../hooks/usePcOnly'
import type { SessionState } from '../../hooks/useSession'
import type { AdminUser, AuditEvent } from '../../types/adminUsers'

export function holds(s: SessionState, permission: string): boolean {
  if (s.status === 'loading') return false
  // Unknown session: render as before sign-in existed (routes still enforce),
  // except admin controls, which would otherwise light up for every member.
  if (s.status === 'unavailable') return !permission.startsWith('admin.')
  return s.me.permissions.includes(permission)
}

/** Show the sections? Only to a caller holding admin.users.read; not while /me is unavailable. */
export function canViewUsers(s: SessionState): boolean {
  return holds(s, 'admin.users.read')
}

/** Show the account buttons? Only with admin.users (every admin session,
 * the household address included; admin rows are still PC-only there). */
export function canChangeUsers(s: SessionState): boolean {
  return holds(s, 'admin.users')
}

export const CHANGES_AT_PC = 'Account changes are made on the main PC.'

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

export const ADMIN_PC_ONLY = 'Admin accounts can only be changed on the main PC.'

/** Why an admin account can't be changed from here: anything touching an
 * admin is PC-only on the server. 'unknown' waits for 'local' like the other
 * Diagnostics admin blocks. */
export function adminTargetBlock(user: AdminUser, pc: PcMode): string | null {
  return user.is_admin && pc !== 'local' ? ADMIN_PC_ONLY : null
}

/** Show "Remove admin…" at all? Only on an admin row, and only on the PC:
 * the server refuses it anywhere else, so remote tabs don't get the button. */
export function showRevokeAdmin(user: AdminUser, pc: PcMode): boolean {
  return user.is_admin && pc === 'local'
}

/** A plain name and what it allows, for each member permission; the server's
 * list decides which are shown, and an unknown one is shown by its own name. */
const RIGHTS: Record<string, { name: string; about: string }> = {
  'library.read': { name: 'See the library', about: 'Browse the library and open the titles they can see.' },
  'lines.read': { name: 'Read subtitles', about: 'Read subtitle lines and download subtitle files.' },
  'lines.edit': { name: 'Edit subtitles', about: 'Change lines, speakers, glossaries and title details on titles they can edit.' },
  'jobs.start': { name: 'Start jobs', about: 'Start transcription, translation, export and other jobs on the PC.' },
  'jobs.cancel': { name: 'Stop jobs', about: 'Stop their own jobs and jobs on titles they own.' },
  'review.use': { name: 'Use Review', about: 'Use the Review tools: line history, notes, versions and checks.' },
  'media.import_url': { name: 'Import from a link', about: 'Add media from a web link, and start Live from a stream link.' },
  'sources.import': { name: 'Import from sources', about: 'Add chapters from novel and comic sites, and follow series for new chapters.' },
  'engines.paid': { name: 'Paid engines', about: 'Use engines that cost money, such as Claude, Gemini, OpenAI, DeepSeek or Ollama cloud models.' },
  'extension.send': { name: 'Browser extension', about: 'Connect the browser extension and send pages to Baihe from their own browser.' },
  'media.stream': { name: 'Play media', about: 'Play audio and video and view comic pages from the PC.' },
}

export function rightName(permission: string): string {
  return RIGHTS[permission]?.name ?? permission
}

export function rightAbout(permission: string): string | null {
  return RIGHTS[permission]?.about ?? null
}

/** Show the rights toggles? Only on an active member account: an admin holds
 * every right through the admin flag, and a deactivated account holds none. */
export function showRights(user: AdminUser): boolean {
  return !user.is_admin && user.is_active
}

/** Why this admin's rights can't be removed now, or null if they can. */
export function revokeAdminBlock(user: AdminUser, users: AdminUser[]): string | null {
  if (user.is_self) return "You can't remove your own admin rights."
  if (user.is_active && activeAdminCount(users) <= 1) {
    return "The last active admin can't lose admin rights. Baihe needs at least one."
  }
  return null
}

/** Each row button's reason to be off (null = allowed); the row's own rules come first. */
export function rowBlocks(user: AdminUser, users: AdminUser[], pc: PcMode) {
  const admin = adminTargetBlock(user, pc)
  return {
    revoke: revokeBlock(user) ?? admin,
    deactivate: deactivateBlock(user, users) ?? admin,
    activate: user.is_active ? null : admin,
    key: admin,
  }
}

export function userName(user: AdminUser): string {
  return user.display_name ? `${user.display_name} (${user.email})` : user.email
}

export function sessionsText(n: number): string {
  return n === 0 ? 'Not signed in' : n === 1 ? 'Signed in on 1 device' : `Signed in on ${n} devices`
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
  'session.revoke_others': 'Other devices signed out',
  'user.add': 'User added',
  'user.activate': 'User activated',
  'user.deactivate': 'User deactivated',
  'user.grant_admin_local': 'Admin granted on the PC',
  'user.revoke_admin': 'Admin rights removed',
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
