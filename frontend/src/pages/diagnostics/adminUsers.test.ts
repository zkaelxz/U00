import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../../api/auth'
import type { AdminUser } from '../../types/adminUsers'
import {
  activeAdminCount, appendAudit, auditActionLabel, auditActor, auditTime, canManageUsers,
  deactivateBlock, revokeBlock, sessionsText, userName,
} from './adminUsers'

const user = (o: Partial<AdminUser> = {}): AdminUser => ({
  id: 1, email: 'a@example.com', display_name: '', is_admin: false, is_active: true,
  has_google_binding: true, created_at: null, permissions: [], active_sessions: 1, is_self: false, ...o,
})

const me = (permissions: string[], auth_enabled = true): AuthMe => ({
  auth_enabled, signed_in: true, sign_in_configured: true, zone: 'internet', permissions,
  user: { id: 1, email: 'a@example.com', display_name: '', is_admin: false, is_local_owner: false },
})

describe('canManageUsers', () => {
  it('needs admin.users; waits while loading; trusts the server when /me is unavailable', () => {
    expect(canManageUsers({ status: 'loading' })).toBe(false)
    expect(canManageUsers({ status: 'unavailable' })).toBe(true)
    expect(canManageUsers({ status: 'ready', me: me(['library.read', 'admin.diagnostics']) })).toBe(false)
    expect(canManageUsers({ status: 'ready', me: me(['admin.users']) })).toBe(true)
  })
})

describe('guards', () => {
  it('refuses deactivating yourself or the last active admin', () => {
    const self = user({ id: 1, is_admin: true, is_self: true })
    const other = user({ id: 2, is_admin: true })
    const member = user({ id: 3 })
    expect(activeAdminCount([self, other, member])).toBe(2)
    expect(deactivateBlock(self, [self, other])).toMatch(/your own account/)
    expect(deactivateBlock(other, [self, other])).toBeNull()
    expect(deactivateBlock(other, [other, member])).toMatch(/last active admin/)
    expect(deactivateBlock(other, [other, user({ id: 4, is_admin: true, is_active: false })])).toMatch(/last/)
    expect(deactivateBlock(member, [other, member])).toBeNull()
    expect(deactivateBlock(user({ is_active: false, is_self: true }), [])).toBeNull()
  })

  it('refuses ending your own sessions, and has nothing to end without one', () => {
    expect(revokeBlock(user({ is_self: true }))).toMatch(/Sign out/)
    expect(revokeBlock(user({ active_sessions: 0 }))).toMatch(/Not signed in/)
    expect(revokeBlock(user({ active_sessions: 2 }))).toBeNull()
  })
})

describe('labels', () => {
  it('names users and actors', () => {
    const u = user({ id: 5, display_name: 'Kid', email: 'kid@example.com' })
    expect(userName(u)).toBe('Kid (kid@example.com)')
    expect(userName(user())).toBe('a@example.com')
    expect(auditActor(5, [u])).toBe('Kid (kid@example.com)')
    expect(auditActor(9, [u])).toBe('User #9')
    expect(auditActor(9, null)).toBe('User #9')
    expect(auditActor(null, [u])).toBe('PC or system')
  })

  it('formats actions, times and session counts', () => {
    expect(auditActionLabel('user.deactivate')).toBe('User deactivated')
    expect(auditActionLabel('something.new')).toBe('something.new')
    expect(auditTime('2026-09-30T12:34:56Z')).toBe('2026-09-30 12:34 UTC')
    expect(auditTime('yesterday')).toBe('yesterday')
    expect([0, 1, 3].map(sessionsText)).toEqual(['not signed in', 'signed in on 1 device', 'signed in on 3 devices'])
  })

  it('appends an older page without repeating rows', () => {
    const e = (id: number) => ({ id, ts: '', user_id: null, action: 'x', detail: '' })
    expect(appendAudit([e(9), e(8)], [e(8), e(7)]).map((x) => x.id)).toEqual([9, 8, 7])
  })
})
