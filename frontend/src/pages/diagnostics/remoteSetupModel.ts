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
