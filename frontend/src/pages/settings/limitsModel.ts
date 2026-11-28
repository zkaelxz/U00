/** Settings > Your limits: a member's own limits as plain lines (/api/auth/me limits). */
import type { MemberLimits } from '../../api/auth'
import type { SessionState } from '../../hooks/useSession'

export const LIMITS_TITLE = 'Your limits'
// Warned this early so a long run isn't started on what is left.
export const NEAR_SHARE = 0.8

export const NEAR_LABEL = 'Almost used up'
export const FULL_LABEL = 'Used up'

// The badge beside a line: none, nearly reached, or reached (the next start is refused).
export type LimitLine = { id: string; text: string; badge: string | null }

export function myLimits(s: SessionState): MemberLimits | null {
  return s.status === 'ready' ? s.me.limits ?? null : null
}

const usd = (n: number) => `$${n.toFixed(2)}`
function badge(used: number, limit: number): string | null {
  if (limit <= 0) return null
  if (used >= limit) return FULL_LABEL
  return used >= limit * NEAR_SHARE ? NEAR_LABEL : null
}

/** Only the limits the PC owner set; the title limit always applies to a member. */
export function limitLines(l: MemberLimits): LimitLine[] {
  const lines: LimitLine[] = []
  if (l.monthly_paid_cap_usd != null) {
    lines.push({ id: 'spend', badge: badge(l.month_paid_spend_usd, l.monthly_paid_cap_usd),
      text: `AI spending this month: ${usd(l.month_paid_spend_usd)} of ${usd(l.monthly_paid_cap_usd)}` })
  }
  if (l.daily_job_limit != null) {
    lines.push({ id: 'jobs', badge: badge(l.jobs_today, l.daily_job_limit),
      text: `Jobs started today: ${l.jobs_today} of ${l.daily_job_limit}` })
  }
  lines.push({ id: 'titles', badge: badge(l.titles_owned, l.title_limit),
    text: `Titles you own: ${l.titles_owned} of ${l.title_limit}` })
  return lines
}
