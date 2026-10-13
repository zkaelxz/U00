import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { useHolds } from '../../hooks/useHolds'
import { routeHref } from '../../router'
import { NeedsInstall } from './NeedsInstall'
import type { Need } from './needs'

export interface ListedNeed {
  need: Need
  /** Stops the run (Missing) or is only suggested (Recommended). */
  blocking: boolean
}

/**
 * The rows of a "Before you run" list for what the settings on screen need: one per unmet need, with
 * its reason and fix, and a calm line for each first-use warning. Shares the preflight card's row layout.
 */
export function NeedRows({ items, warnings = [], onInstalled }: { items: ListedNeed[]; warnings?: string[]; onInstalled?: () => void }) {
  const admin = useHolds('admin.diagnostics')
  return (
    <>
      {items.map(({ need, blocking }) => (
        <li key={need.id} className="preflight-row" data-testid={`preflight-${need.id}`}>
          <strong className="preflight-label">{need.label}</strong>
          <span className="preflight-state">
            <Badge tone={blocking ? 'warn' : 'info'}>{blocking ? 'Missing' : 'Recommended'}</Badge>
          </span>
          <div className="preflight-action">
            <NeedsInstall need={need} onInstalled={onInstalled} />
          </div>
        </li>
      ))}
      {warnings.map((w) => (
        <li key={w} className="preflight-row" data-testid="preflight-warning">
          <strong className="preflight-label">First use</strong>
          <span className="preflight-state"><Badge tone="info">Heads up</Badge></span>
          <div className="preflight-action"><p className="muted">{w}</p></div>
        </li>
      ))}
      {admin && items.length > 0 && (
        <li className="preflight-row" data-testid="preflight-recommended-link">
          <span className="preflight-label muted">Setting up a new PC?</span>
          <span />
          <div className="preflight-action">
            <ButtonLink variant="ghost" size="sm" href={routeHref({ name: 'diagnostics', card: 'recommended' })}>See all recommended installs</ButtonLink>
          </div>
        </li>
      )}
    </>
  )
}
