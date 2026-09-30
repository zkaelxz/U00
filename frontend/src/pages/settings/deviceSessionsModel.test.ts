import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../../api/auth'
import type { SessionState } from '../../hooks/useSession'
import type { DeviceSession } from '../../types/deviceSessions'
import {
  agoText,
  deviceDetails,
  deviceName,
  networkText,
  otherDevices,
  showDevices,
  signOutOthersBlock,
  signedOutNote,
  timeoutText,
} from './deviceSessionsModel'

const NOW = Date.UTC(2026, 8, 30, 12, 0, 0)
const sec = NOW / 1000

const dev = (o: Partial<DeviceSession>): DeviceSession => ({
  id: 1, device: 'Chrome on Android', created_at: sec - 3 * 86400, last_seen_at: sec - 300,
  expires_at: sec + 86400, ip_prefix: '203.0.113', current: false, ...o,
})

const me = (o: Partial<AuthMe>): SessionState => ({
  status: 'ready',
  me: {
    auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions: [],
    user: { id: 7, email: 'k@example.com', display_name: 'K', is_admin: false, is_local_owner: false },
    ...o,
  },
})

describe('signed-in devices helpers', () => {
  it('shows the card only to a signed-in person', () => {
    expect(showDevices(me({}))).toBe(true)
    expect(showDevices(me({ auth_enabled: false }))).toBe(false)
    expect(showDevices(me({ signed_in: false, user: null }))).toBe(false)
    expect(showDevices(me({ user: { id: null, email: null, display_name: 'PC', is_admin: true, is_local_owner: true } }))).toBe(false)
    expect(showDevices({ status: 'loading' })).toBe(false)
    expect(showDevices({ status: 'unavailable' })).toBe(false)
  })

  it('words the coarse network prefix', () => {
    expect(networkText('203.0.113')).toBe('network 203.0.113.x')
    expect(networkText('2001:db8:1::/48')).toBe('network 2001:db8:1::/48')
    expect(networkText('')).toBe('network unknown')
  })

  it('says how long ago', () => {
    expect(agoText(sec - 30, NOW)).toBe('just now')
    expect(agoText(sec - 600, NOW)).toBe('10 minutes ago')
    expect(agoText(sec - 3600, NOW)).toBe('1 hour ago')
    expect(agoText(sec - 5 * 3600, NOW)).toBe('5 hours ago')
    expect(agoText(sec - 86400, NOW)).toBe('yesterday')
    expect(agoText(sec - 4 * 86400, NOW)).toBe('4 days ago')
    expect(agoText(sec + 60, NOW)).toBe('just now') // a clock slightly ahead
  })

  it('describes a device, this one as in use', () => {
    expect(deviceName(dev({}))).toBe('Chrome on Android (network 203.0.113.x)')
    expect(deviceDetails(dev({}), NOW)).toBe('Last used 5 minutes ago · signed in 3 days ago · network 203.0.113.x')
    expect(deviceDetails(dev({ current: true }), NOW)).toBe('In use now · signed in 3 days ago · network 203.0.113.x')
  })

  it('blocks "sign out others" when this is the only device', () => {
    const here = dev({ id: 1, current: true })
    expect(signOutOthersBlock([here])).toBe('No other devices are signed in.')
    expect(signOutOthersBlock([here, dev({ id: 2 })])).toBeNull()
    expect(otherDevices([here, dev({ id: 2 })]).map((d) => d.id)).toEqual([2])
  })

  it('words timeouts and results', () => {
    expect(timeoutText(14, 30)).toBe('A device is signed out after 14 days without use, and 30 days after it signed in.')
    expect(timeoutText(1, 1)).toBe('A device is signed out after 1 day without use, and 1 day after it signed in.')
    expect(signedOutNote(0)).toBe('No other devices were signed in.')
    expect(signedOutNote(1)).toBe('Signed out 1 other device.')
    expect(signedOutNote(3)).toBe('Signed out 3 other devices.')
  })
})
