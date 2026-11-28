import { describe, expect, it } from 'vitest'

import { backupBannerWanted } from './backupBannerGate'
import { isMember, resetSessionForTests, getSession } from '../hooks/useSession'
import type { AuthMe } from '../api/auth'

const me = (user: Partial<NonNullable<AuthMe['user']>>) =>
  ({ auth_enabled: true, signed_in: true, user: { is_admin: false, is_local_owner: false, ...user } }) as AuthMe

describe('backupBannerWanted', () => {
  it('hides the owner warning from a signed-in member at the PC', () => {
    resetSessionForTests({ status: 'ready', me: me({}) })
    expect(backupBannerWanted('local', isMember(getSession()))).toBe(false)
  })

  it('keeps it for an admin and the PC owner, and away from the PC hides it', () => {
    resetSessionForTests({ status: 'ready', me: me({ is_admin: true }) })
    expect(backupBannerWanted('local', isMember(getSession()))).toBe(true)
    resetSessionForTests({ status: 'ready', me: me({ is_local_owner: true }) })
    expect(backupBannerWanted('local', isMember(getSession()))).toBe(true)
    expect(backupBannerWanted('remote', false)).toBe(false)
  })
})
