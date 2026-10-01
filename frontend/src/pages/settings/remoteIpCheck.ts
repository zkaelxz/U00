// Pure text helpers for Settings > Remote access (RemoteAccessSection.tsx):
// the public-address check. Nothing here ever sees a saved address: the API
// returns `configured`, and Test a state and a fixed message.
import { ApiError } from '../../api/client'
import type { BadgeTone } from '../../components/labels'
import type { RemoteIpCheckTestResult } from '../../types/diagnostics'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

export const IP_CHECK_PLACEHOLDER = 'https://api.ipify.org'
export const IP_CHECK_MAX = 512

/** A quick check before sending (the server checks again, and resolves the name). */
export function draftProblem(draft: string): string | null {
  const v = draft.trim()
  if (!v) return null
  if (v.length > IP_CHECK_MAX) return 'The address is too long.'
  if (!/^https:\/\/[^\s/@]+(\/\S*)?$/i.test(v)) return 'The address must start with https:// and have no spaces.'
  return null
}

const TEST_LABELS: Record<string, { tone: BadgeTone; label: string }> = {
  ok: { tone: 'ok', label: 'OK' },
  warn: { tone: 'warn', label: 'Needs attention' },
  critical: { tone: 'bad', label: 'Not working' },
  unknown: { tone: 'neutral', label: 'Could not tell' },
  not_configured: { tone: 'neutral', label: 'Not set up' },
  off: { tone: 'neutral', label: 'Off' },
}

export function testBadge(r: Pick<RemoteIpCheckTestResult, 'state'>): { tone: BadgeTone; label: string } {
  return TEST_LABELS[r.state] ?? TEST_LABELS.unknown
}

// One plain line. A 422 message from the server is a fixed string that never
// echoes the address, so it is safe to show.
export function ipCheckErrorMessage(err: unknown, writing = true): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return writing ? KEY_WRITES_REFUSED : 'This only works on the main PC.'
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if (err.status === 409) return 'A test is already running.'
    if (err.status === 429) return 'Wait a few seconds before testing again.'
    if (err.status === 422 && err.message) return err.message
  }
  return 'That did not work. Try again.'
}
