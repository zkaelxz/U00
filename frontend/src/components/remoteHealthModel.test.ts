import { describe, expect, it } from 'vitest'

import type { RemoteHealth, RemoteHealthState } from '../types/diagnostics'
import {
  DISMISS_KEY, bannerTitle, checkedLine, diagnosticsText, readDismissed, remoteHealthLabel, remoteHealthTone,
  showBanner, stateKey, writeDismissed,
} from './remoteHealthModel'

function health(state: RemoteHealthState, o: Partial<RemoteHealth> = {}): RemoteHealth {
  return {
    state,
    message: state === 'ok' ? 'Remote access is working.' : 'The certificate expires in 9 days and should already have been renewed.',
    checked_at: 1_800_000_000,
    since: 1_799_990_000,
    certificate: { state: state === 'off' ? 'off' : 'ok', message: '', days_left: state === 'ok' ? 60 : 9 },
    ddns: { state: 'not_configured', message: '', configured: false },
    listener: { state: 'ok', message: '' },
    ...o,
  }
}

function memoryStore(): Storage {
  const m = new Map<string, string>()
  return {
    get length() {
      return m.size
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, v),
  }
}

describe('remote health banner', () => {
  it('shows only for warn and critical', () => {
    expect(showBanner(null, null)).toBe(false)
    for (const s of ['off', 'unknown', 'ok'] as const) expect(showBanner(health(s), null)).toBe(false)
    expect(showBanner(health('warn'), null)).toBe(true)
    expect(showBanner(health('critical'), null)).toBe(true)
  })

  it('a dismissal lasts only until the state changes', () => {
    const warn = health('warn')
    expect(showBanner(warn, stateKey(warn))).toBe(false)
    // Worse: a new state, so it comes back.
    const critical = health('critical', { since: 1_800_000_500 })
    expect(showBanner(critical, stateKey(warn))).toBe(true)
    // Fixed, then broken again later: a new episode (new `since`), shown again.
    const again = health('warn', { since: 1_800_100_000 })
    expect(showBanner(again, stateKey(warn))).toBe(true)
    // A later check of the same episode stays dismissed.
    expect(showBanner(health('warn', { checked_at: 1_800_020_000 }), stateKey(warn))).toBe(false)
  })

  it('remembers the dismissed episode in storage', () => {
    const store = memoryStore()
    expect(readDismissed(store)).toBeNull()
    writeDismissed('warn:5', store)
    expect(store.getItem(DISMISS_KEY)).toBe('warn:5')
    expect(readDismissed(store)).toBe('warn:5')
    expect(readDismissed(null)).toBeNull()
  })

  it('titles, labels and tones', () => {
    expect(bannerTitle(health('critical'))).toBe('Remote access is not working')
    expect(bannerTitle(health('warn'))).toBe('Remote access needs attention')
    expect(remoteHealthLabel('ok')).toBe('OK')
    expect(remoteHealthLabel('off')).toBe('Off')
    expect(remoteHealthTone('ok')).toBe('ok')
    expect(remoteHealthTone('warn')).toBe('warn')
    expect(remoteHealthTone('critical')).toBe('bad')
    expect(remoteHealthTone('off')).toBe('neutral')
  })

  it('Diagnostics text and last-checked line', () => {
    expect(diagnosticsText(health('off'))).toMatch(/not set up on this PC/)
    expect(diagnosticsText(health('ok'))).toBe('Remote access is working. Certificate valid for 60 more days.')
    expect(diagnosticsText(health('warn'))).toMatch(/expires in 9 days/)
    expect(checkedLine(health('ok', { checked_at: null }))).toBe('Not checked yet.')
    expect(checkedLine(health('ok'), 1_800_000_000 + 300)).toBe('Last checked 5 min ago.')
  })
})
