// Pure helpers for the Users and Audit log sections of Diagnostics. The
// server enforces every rule below (409, or 403 for an admin target off the
// PC); these only explain up front why a button is off.
import type { PcMode } from '../../hooks/usePcOnly'
import type { SessionState } from '../../hooks/useSession'
import type { AdminUser, AuditEvent } from '../../types/adminUsers'
import { agoText } from '../settings/deviceSessionsModel'

export function holds(s: SessionState, permission: string): boolean {
  if (s.status === 'loading') return false
  // Unknown session: render as before sign-in existed (routes still enforce),
  // except admin controls (and library.create, which stays hidden until /me answers), which would otherwise
  // light up for every member.
  if (s.status === 'unavailable') return !permission.startsWith('admin.') && permission !== 'library.create'
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

/** Delete is for a deactivated member, from any device an admin is signed in on. */
export function showDeleteUser(user: AdminUser): boolean {
  return !user.is_active && !user.is_admin && !user.is_self
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
  'media.import_url': { name: 'Import from a link', about: 'Add media from a web link, start Live from a stream link, and make subtitles from a video link with One-click translate. These run on the PC; a paid engine also needs Paid engines and stays under the monthly cap and their own limit.' },
  'sources.import': { name: 'Import from sources', about: 'Add chapters from novel and comic sites, and follow series for new chapters.' },
  'engines.paid': { name: 'Paid engines', about: 'Use engines that cost money, such as Claude, Gemini, OpenAI, DeepSeek or Ollama cloud models other than Gemma 31B cloud, which everyone may use.' },
  'media.upload_member': { name: 'Upload audio and video', about: 'Send audio and video files from their phone into titles they own, up to the member upload limit in Settings (1 GB unless changed). Off unless you turn it on.' },
  'extension.send': { name: 'Browser extension and Baihe Grab', about: 'Send pages to Baihe from the browser extension or from Baihe Grab on their phone.' },
  'library.create': { name: 'Add titles', about: 'Add titles of their own, in the Library and in Discover (up to 200), and import them from the Discover catalogue.' },
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

/** "Last seen 3 hours ago", or why there is no time to show. */
export function lastSeenText(u: { last_seen_at?: number | null }, nowMs: number): string {
  return u.last_seen_at == null ? 'No sign-in on record' : `Last seen ${agoText(u.last_seen_at, nowMs)}`
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
  'session.revoke_device': 'One device signed out by an admin',
  'session.revoke_others': 'Other devices signed out',
  'user.add': 'User added',
  'user.activate': 'User activated',
  'user.deactivate': 'User deactivated',
  'user.delete': 'User deleted',
  'user.grant_admin_local': 'Admin granted on the PC',
  'user.revoke_admin': 'Admin rights removed',
  'user.bind_google': 'Google account linked',
  'permission.grant': 'Permission granted',
  'permission.revoke': 'Permission removed',
  'user.paid_limit': 'Paid-engine limit changed',
  'upload.away': 'Upload from away',
  'remote_uploads.on': 'Uploads from away turned on',
  'remote_uploads.off': 'Uploads from away turned off',
  'remote_uploads.auto_off': 'Uploads from away set to turn off after 7 days',
  'remote_uploads.stay_on': 'Uploads from away set to stay on until turned off',
  'library.restore': 'Library restored',
  'library.restore_drama': 'Title restored from backup',
  'audit.prune': 'Old activity cleared',
  'user.rename': 'Person renamed',
  'user.job_limit': 'Daily job limit changed',
  'session.sweep': 'Expired sessions cleared',
  'member_key.save': 'Own Ollama key saved',
  'member_key.remove': 'Own Ollama key removed',
  'app.restart': 'Baihe restarted',
  'app.update': 'Baihe update started',
  'title.delete': 'Title deleted',
  'series.delete': 'Series deleted',
  'series.auto_pipeline': 'Series auto-run changed',
  'series.apply_defaults': 'Series defaults applied to its titles',
  'library.import_dramas': 'Titles imported from a backup',
  'sharing.set_private': 'Title or series sharing changed',
  'sharing.set_owner': 'Owner changed',
  'device_token.create': 'Remote access key added',
  'device_token.revoke': 'Remote access key revoked',
  'device_token.admin_revoke': 'Remote access key revoked by an admin',
  'device_token.revoke_all': 'All remote access keys revoked',
  'pc_profile.set_pin': 'PC profile PIN set',
  'pc_profile.clear_pin': 'PC profile PIN removed',
  'pc_profile.hide_existing': 'Existing titles and series made private',
}

const dollars = (usd: number) => `$${usd.toFixed(2)}`

/** This month's paid-engine spending on a People row, with the member's own limit when set. */
export function paidSpendText(u: AdminUser): string {
  const spent = u.month_paid_spend_usd ?? 0
  const cap = u.monthly_paid_cap_usd ?? null
  if (cap !== null) {
    const over = spent >= cap ? ' (limit reached, so paid engines are off for them)' : ''
    return `Paid engines this month: ${dollars(spent)} of their ${dollars(cap)} limit${over}`
  }
  return spent > 0 ? `Paid engines this month: ${dollars(spent)}` : 'No paid-engine spending this month'
}

export const MAX_JOB_LIMIT = 10000

/** The daily job limit typed on a People row: a whole number from 1 up, else null. */
export function parseJobLimit(text: string): number | null {
  const t = text.trim()
  if (!/^\d+$/.test(t)) return null
  const n = Number(t)
  return n > 0 && n <= MAX_JOB_LIMIT ? n : null
}

/** "Jobs started today: 3 of their 10 a day", or just the count when they have no limit. */
export function jobsTodayText(u: { daily_job_limit?: number | null; jobs_today?: number }): string {
  const n = u.jobs_today ?? 0
  const limit = u.daily_job_limit ?? null
  if (limit === null) return `Jobs started today: ${n}`
  return `Jobs started today: ${n} of their ${limit} a day${n >= limit ? ' (limit reached until tomorrow)' : ''}`
}

// api/schemas/extension_access.AdminAddUserRequest
export const MAX_EMAIL_CHARS = 254
export const MAX_DISPLAY_NAME_CHARS = 100

// services/auth_service.MAX_MEMBER_PAID_CAP_USD
export const MAX_PAID_LIMIT_USD = 100000

/** The limit typed on a People row: a positive dollar amount, else null. */
export function parsePaidLimit(text: string): number | null {
  const t = text.trim().replace(/^\$/, '')
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null
  const n = Number(t)
  return n > 0 && n <= MAX_PAID_LIMIT_USD ? n : null
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

/** What a self-saving field read from its text: the value to send, or why it can't be sent. */
export type Parsed<T> = { value: T } | { problem: string }

export function readDisplayName(text: string): Parsed<string> {
  return { value: text.trim().replace(/\s+/g, ' ') }
}

/** Empty means no limit, so clearing the field is how a limit is removed. */
export function readPaidLimit(text: string): Parsed<number | null> {
  if (!text.trim()) return { value: null }
  const usd = parsePaidLimit(text)
  return usd === null
    ? { problem: `Enter an amount above $0 and up to $${MAX_PAID_LIMIT_USD.toLocaleString('en-US')}, such as 5 or 12.50, or leave it empty for no limit.` }
    : { value: usd }
}

export function readJobLimit(text: string): Parsed<number | null> {
  if (!text.trim()) return { value: null }
  const jobs = parseJobLimit(text)
  return jobs === null
    ? { problem: `Enter a whole number from 1 to ${MAX_JOB_LIMIT.toLocaleString('en-US')}, such as 10, or leave it empty for no limit.` }
    : { value: jobs }
}

/** The short muted line on a person's closed row: sign-in state, then any limits they have. */
export function personStatus(u: AdminUser): string {
  const parts = [sessionsText(u.active_sessions)]
  if (showRights(u)) {
    if (u.monthly_paid_cap_usd != null) parts.push(`${dollars(u.monthly_paid_cap_usd)} a month`)
    if (u.daily_job_limit != null) parts.push(`${u.daily_job_limit} ${u.daily_job_limit === 1 ? 'job' : 'jobs'} a day`)
  }
  return parts.join(' · ')
}

/** A rights-grid column heading: the name when there is one, else the email. */
export function personShortName(u: AdminUser): string {
  return u.display_name || u.email
}

/** Below this many people the list fits a screen, so there is no search box. */
export const FIND_PEOPLE_AT = 8

export function matchesPerson(u: AdminUser, query: string): boolean {
  const q = query.trim().toLowerCase()
  return !q || u.email.toLowerCase().includes(q) || (u.display_name ?? '').toLowerCase().includes(q)
}
