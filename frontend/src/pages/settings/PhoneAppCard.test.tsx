import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ALERTS_BLOCKED, PhoneAppCard } from './PhoneAppCard'

afterEach(() => { vi.unstubAllGlobals() })

const render = (alertsOn: boolean, blocked: boolean) => {
  vi.stubGlobal('window', {
    BaiheShell: { get: (n: string) => n !== 'alerts_on' || alertsOn, set: vi.fn(), notificationsBlocked: () => blocked },
  })
  return renderToStaticMarkup(<PhoneAppCard />)
}

describe('PhoneAppCard', () => {
  it('says Android is blocking notifications while the alerts switch is on', () => {
    expect(render(true, true)).toContain(ALERTS_BLOCKED.slice(0, 40))
  })

  it('says nothing when notifications are allowed or alerts are off', () => {
    expect(render(true, false)).not.toContain(ALERTS_BLOCKED.slice(0, 40))
    expect(render(false, true)).not.toContain(ALERTS_BLOCKED.slice(0, 40))
  })
})
