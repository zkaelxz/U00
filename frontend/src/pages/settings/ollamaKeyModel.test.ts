import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../../api/auth'
import type { SessionState } from '../../hooks/useSession'
import { keyProblem, keyStateText, showOllamaKey } from './ollamaKeyModel'

const NOW = Date.UTC(2026, 9, 14, 12, 0, 0)

const me = (o: Partial<AuthMe>, user: Partial<NonNullable<AuthMe['user']>> = {}): SessionState => ({
  status: 'ready',
  me: {
    auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet',
    user: { id: 2, email: 'kid@example.com', display_name: 'Kid', is_admin: false, is_local_owner: false, ...user },
    permissions: ['jobs.start'], ...o,
  },
})

describe('showOllamaKey', () => {
  it('is for a signed-in member who may start jobs', () => {
    expect(showOllamaKey(me({}))).toBe(true)
    expect(showOllamaKey(me({ permissions: ['library.read'] }))).toBe(false)
  })
  it('is hidden for the owner at the PC and while loading', () => {
    expect(showOllamaKey(me({ auth_enabled: false }))).toBe(false)
    expect(showOllamaKey(me({}, { is_local_owner: true }))).toBe(false)
    expect(showOllamaKey({ status: 'loading' })).toBe(false)
  })
})

describe('keyProblem', () => {
  it('refuses empty, spaced, short and long values', () => {
    expect(keyProblem('  ')).toMatch(/Paste/)
    expect(keyProblem('abcd efgh ijkl mnop')).toMatch(/spaces/)
    expect(keyProblem('abc123')).toMatch(/too short/)
    expect(keyProblem('x'.repeat(513))).toMatch(/too long/)
    expect(keyProblem(' 0123456789abcdef0123 ')).toBeNull()
  })
})

describe('keyStateText', () => {
  it('shows only the last four characters', () => {
    const t = keyStateText({ has_key: true, last4: 'WxYz', added_at: NOW / 1000 - 3600, unreadable: false }, NOW)
    expect(t).toMatch(/^Key ending in WxYz/)
  })
  it('asks to paste an unreadable key again', () => {
    expect(keyStateText({ has_key: false, last4: 'WxYz', added_at: 1, unreadable: true }, NOW)).toMatch(/Paste it again/)
  })
  it('says what happens without a key', () => {
    expect(keyStateText({ has_key: false, last4: null, added_at: null, unreadable: false }, NOW)).toMatch(/No key saved/)
  })
})
