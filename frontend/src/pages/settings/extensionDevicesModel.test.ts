import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../../api/auth'
import type { SessionState } from '../../hooks/useSession'
import type { DeviceToken } from '../../types/extensionDevices'
import {
  ADMIN_TOKENS_PC_ONLY,
  activeCount,
  adminTokensBlock,
  extensionDevicesView,
  labelProblem,
  tokenDetails,
} from './extensionDevicesModel'

const NOW = Date.UTC(2026, 9, 8, 12, 0, 0)
const sec = NOW / 1000

const tok = (o: Partial<DeviceToken>): DeviceToken => ({
  id: 1, label: 'Work laptop', created_at: sec - 3 * 86400, last_used_at: sec - 600,
  last_used_ip_prefix: '203.0.113', expires_at: sec + 80 * 86400, revoked_at: null, status: 'active', ...o,
})

const me = (o: Partial<AuthMe>): SessionState => ({
  status: 'ready',
  me: {
    auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions: ['library.read'],
    user: { id: 7, email: 'k@example.com', display_name: 'K', is_admin: false, is_local_owner: false },
    ...o,
  },
})
const pcOwner = me({
  auth_enabled: false, zone: 'pc', permissions: ['admin.settings'],
  user: { id: null, email: null, display_name: 'PC', is_admin: true, is_local_owner: true },
})

describe('extension devices helpers', () => {
  it('shows own devices to a signed-in person and everyone\'s to an admin at the PC', () => {
    expect(extensionDevicesView(me({}), 'remote')).toEqual({ own: true, canCreate: false, all: false })
    expect(extensionDevicesView(me({ permissions: ['extension.send'] }), 'remote'))
      .toEqual({ own: true, canCreate: true, all: false })
    expect(extensionDevicesView(pcOwner, 'local')).toEqual({ own: false, canCreate: false, all: true })
    // Everyone's devices are PC only, and wait for the PC check.
    expect(extensionDevicesView(pcOwner, 'unknown')).toEqual({ own: false, canCreate: false, all: false })
    expect(extensionDevicesView(pcOwner, 'remote')).toEqual({ own: false, canCreate: false, all: false })
    const admin = me({ user: { id: 1, email: 'a@example.com', display_name: 'A', is_admin: true, is_local_owner: false } })
    expect(extensionDevicesView(admin, 'local').all).toBe(true)
    expect(extensionDevicesView(admin, 'remote').all).toBe(false)
    expect(extensionDevicesView(me({ signed_in: false, user: null }), 'remote').own).toBe(false)
    expect(extensionDevicesView({ status: 'loading' }, 'local')).toEqual({ own: false, canCreate: false, all: false })
  })

  it('blocks an admin account away from the PC', () => {
    const admin = me({ user: { id: 1, email: 'a@example.com', display_name: 'A', is_admin: true, is_local_owner: false } })
    expect(adminTokensBlock(admin, 'remote')).toBe(ADMIN_TOKENS_PC_ONLY)
    expect(adminTokensBlock(admin, 'unknown')).toBe(ADMIN_TOKENS_PC_ONLY)
    expect(adminTokensBlock(admin, 'local')).toBeNull()
    expect(adminTokensBlock(me({}), 'remote')).toBeNull()
  })

  it('checks the device name like the server', () => {
    expect(labelProblem('  ')).toMatch(/Name the device/)
    expect(labelProblem('x'.repeat(41))).toMatch(/at most 40/)
    expect(labelProblem('  Work   laptop ')).toBeNull()
    expect(labelProblem('x'.repeat(40))).toBeNull()
  })

  it('words each state', () => {
    expect(tokenDetails(tok({}), NOW)).toBe('Last used 10 minutes ago · network 203.0.113.x · added 3 days ago · expires in 80 days')
    expect(tokenDetails(tok({ last_used_at: null, last_used_ip_prefix: '', expires_at: null }), NOW))
      .toBe('Never used · added 3 days ago · never expires')
    expect(tokenDetails(tok({ expires_at: sec + 3600 }), NOW)).toMatch(/expires within a day$/)
    expect(tokenDetails(tok({ status: 'revoked', revoked_at: sec - 86400 }), NOW)).toBe('Revoked yesterday')
    expect(tokenDetails(tok({ status: 'expired' }), NOW)).toBe('Expired')
  })

  it('counts active devices only', () => {
    expect(activeCount([tok({}), tok({ id: 2, status: 'revoked' }), tok({ id: 3, status: 'expired' })])).toBe(1)
  })
})
