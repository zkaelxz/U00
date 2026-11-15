// Pure text helpers for People and access > Set up remote access (RemoteSetupSection.tsx).
import type { BadgeTone } from '../../components/labels'
import type { RemoteSetup, RemoteSetupStep } from '../../types/diagnostics'

const BADGES: Record<RemoteSetupStep['state'], { tone: BadgeTone; label: string }> = {
  done: { tone: 'ok', label: 'Done' },
  todo: { tone: 'warn', label: 'To do' },
  problem: { tone: 'bad', label: 'Needs a fix' },
  manual: { tone: 'neutral', label: 'Yours to check' },
}

export const stepBadge = (s: Pick<RemoteSetupStep, 'state'>) => BADGES[s.state]

/** The closed-card line: how far along, and what comes next. */
export function setupSummary(setup: RemoteSetup): string {
  const done = setup.steps.filter((s) => s.state === 'done').length
  const next = setup.steps.find((s) => s.id === setup.next)
  return next ? `${done} of ${setup.steps.length} done · next: ${next.title}` : 'All steps done'
}

/** The address to give Google, built from what was typed: the name never comes back from the API. */
export function redirectAddress(nameDraft: string): string | null {
  const host = nameDraft.trim().replace(/^https:\/\//i, '').replace(/\/+$/, '')
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i.test(host)) return null
  return `https://${host.toLowerCase()}/api/auth/callback`
}

/** A quick check before sending (the server checks again). */
export function signInProblem(draft: { name: string; id: string; secret: string }): string | null {
  if (draft.name.trim() && !redirectAddress(draft.name)) return 'The name must be like baihe.your-domain, with no port or path.'
  if (draft.id.trim() && !draft.id.trim().endsWith('.apps.googleusercontent.com')) {
    return 'A Google client id ends with .apps.googleusercontent.com. The secret goes in the other box.'
  }
  if (/\s/.test(draft.id.trim()) || /\s/.test(draft.secret.trim())) return 'No spaces are allowed in the id or the secret.'
  return null
}
