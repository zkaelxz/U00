/*
 * PaidLimitBanner: in the app shell, for a member whose own monthly limit for paid engines is used up
 * (/api/auth/me paid_limit_reached). The paid engines then vanish from the pickers, so this says why.
 */
import { useSession } from '../hooks/useSession'

export const PAID_LIMIT_BANNER =
  "You've used this month's limit for paid engines, so they're off for you until next month or until the " +
  'person who runs Baihe on the PC raises your limit. Free engines such as Ollama still work.'

export function PaidLimitBanner() {
  const s = useSession()
  if (s.status !== 'ready' || !s.me.paid_limit_reached) return null
  return (
    <div className="banner banner-prose paid-limit-banner" role="status" data-testid="paid-limit-banner">
      {PAID_LIMIT_BANNER}
    </div>
  )
}
