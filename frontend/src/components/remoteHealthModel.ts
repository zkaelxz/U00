// Pure helpers for the remote-access health banner (RemoteHealthBanner.tsx)
// and the Diagnostics line: when to show the banner, what it says, and the
// dismissal, which lasts only until the state changes. Unit-tested in
// remoteHealthModel.test.ts.
import type { RemoteHealth, RemoteHealthState } from '../types/diagnostics'
import type { BadgeTone } from './labels'
import { shortAgo } from './notificationBellState'

export const DISMISS_KEY = 'baihe.remoteHealth.dismissed'
// The server checks every few hours; this only picks the result up.
export const POLL_MS = 5 * 60_000

const isProblem = (h: Pick<RemoteHealth, 'state'>) => h.state === 'warn' || h.state === 'critical'

/** Identifies one state episode: a new state (or the same state starting again) gets a new key. */
export function stateKey(h: Pick<RemoteHealth, 'state' | 'since'>): string {
  return `${h.state}:${h.since ?? ''}`
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export function readDismissed(store: Storage | null = storage()): string | null {
  try {
    return store?.getItem(DISMISS_KEY) ?? null
  } catch {
    return null
  }
}

export function writeDismissed(key: string, store: Storage | null = storage()): void {
  try {
    store?.setItem(DISMISS_KEY, key)
  } catch {
    // Storage blocked: dismissed until the page reloads.
  }
}

/** Shown for warn/critical unless this very state episode was dismissed. */
export function showBanner(h: RemoteHealth | null, dismissed: string | null): boolean {
  return !!h && isProblem(h) && dismissed !== stateKey(h)
}

export function bannerTitle(h: Pick<RemoteHealth, 'state'>): string {
  return h.state === 'critical' ? 'Remote access is not working' : 'Remote access needs attention'
}

const LABELS: Record<RemoteHealthState, string> = {
  off: 'Off',
  unknown: 'Not checked yet',
  ok: 'OK',
  warn: 'Needs attention',
  critical: 'Not working',
}

const TONES: Record<RemoteHealthState, BadgeTone> = {
  off: 'neutral',
  unknown: 'neutral',
  ok: 'ok',
  warn: 'warn',
  critical: 'bad',
}

export const remoteHealthLabel = (s: RemoteHealthState) => LABELS[s] ?? 'Not checked yet'
export const remoteHealthTone = (s: RemoteHealthState): BadgeTone => TONES[s] ?? 'neutral'

export function checkedLine(h: Pick<RemoteHealth, 'checked_at'>, nowSec: number = Date.now() / 1000): string {
  return h.checked_at == null ? 'Not checked yet.' : `Last checked ${shortAgo(h.checked_at, nowSec)}.`
}

/** The Diagnostics line after the badge. */
export function diagnosticsText(h: RemoteHealth): string {
  if (h.state === 'off') return 'Remote access is not set up on this PC, so nothing is checked.'
  if (h.state === 'ok' && h.certificate.days_left != null) {
    return `${h.message} Certificate valid for ${h.certificate.days_left} more days.`
  }
  return h.message
}
