// Pure decisions about an install plan, kept apart from the components that render it.
import type { PendingInstallPlan, PendingInstallStatus } from '../../types/pendingInstall'

// A plain preview is only worth a click when something is replaced, risky,
// refused or has to wait; a fresh install of new packages goes straight ahead.
export function needsPlanPanel(plan: PendingInstallPlan): boolean {
  return plan.blocked.length > 0 || plan.mode === 'restart' || plan.needs_confirm.length > 0
    || plan.changes.some((c) => c.kind !== 'install')
}

// Only "this server has no plan endpoint" (404) or "no answer at all" may skip the
// preview. Anything else (a 409 while a job runs, a 403, a dry-run timeout, a
// 5xx) means the check itself did not happen, and installing on files that may
// be in use is what the preview exists to prevent.
export function planCheckUnavailable(e: unknown): boolean {
  if (e instanceof TypeError) return true
  const err = e as { status?: number; code?: string } | null
  return err?.status === 404 || (err?.status === 0 && err.code === 'network_error')
}

// A panel for a different set of packages must start unticked: an "I understand"
// given for one downgrade is not consent for another.
export const planPanelKey = (keys: string[]): string => keys.join('\u0000')

export function pendingKeys(status: PendingInstallStatus | null): Set<string> {
  return new Set(status?.packages ?? [])
}
