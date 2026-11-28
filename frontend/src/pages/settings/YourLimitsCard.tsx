/*
 * Settings > Your limits: a signed-in member's daily jobs, monthly AI spending and titles against
 * the limits the PC owner set, so a refusal is not the first they hear of one.
 */
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { useSession } from '../../hooks/useSession'
import { FULL_LABEL, LIMITS_TITLE, limitLines, myLimits } from './limitsModel'

export function YourLimitsCard() {
  const limits = myLimits(useSession())
  if (!limits) return null
  return (
    <Card title={LIMITS_TITLE} aria-label={LIMITS_TITLE}>
      {limitLines(limits).map((l) => (
        <p key={l.id} data-testid={`limit-${l.id}`}>
          {l.text}{l.badge && <> <Badge tone={l.badge === FULL_LABEL ? 'bad' : 'warn'}>{l.badge}</Badge></>}
        </p>
      ))}
    </Card>
  )
}
