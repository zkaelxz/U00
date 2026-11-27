/*
 * PaidLimitBanner: in the app shell, for a member whose own monthly limit for paid engines is used up
 * (/api/auth/me paid_limit_reached). The paid engines then vanish from the pickers, so this says why.
 */
import { useSession } from '../hooks/useSession'
import { WarnBar } from './WarnBar'

export const PAID_LIMIT_BANNER =
  "You've reached your monthly spending limit, so AI runs are paused for you until next month or until the " +
  'person who runs Baihe on the PC raises your limit.'

export function PaidLimitBanner() {
  const s = useSession()
  if (s.status !== 'ready' || !s.me.paid_limit_reached) return null
  return (
    <WarnBar shell testId="paid-limit-banner" title={PAID_LIMIT_BANNER}>
      {PAID_LIMIT_BANNER}
    </WarnBar>
  )
}
