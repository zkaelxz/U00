/*
 * Settings > Menu items: a switch per menu item this person can see.
 * Hiding only tidies the menu for them; the page still opens by its address.
 * Library and Settings are shown but locked on.
 */
import { usePcOnly } from '../../hooks/usePcOnly'
import { Card } from '../../components/Card'
import { Toggle } from '../../components/Toggle'
import { useSession } from '../../hooks/useSession'
import { useHiddenNav } from '../../nav/hiddenNav'
import { customizableNavItems, isNavLocked, navId } from '../../nav/navItems'
import { useDeveloperMode } from '../assistant/developerMode'
import { useViewMode } from '../../nav/viewMode'

export function CustomizeMenuCard() {
  const session = useSession()
  const pcMode = usePcOnly()
  const developerMode = useDeveloperMode(true)
  const simple = useViewMode() === 'simple'
  const [hidden, setHidden] = useHiddenNav(session)
  const items = customizableNavItems({ session, pcMode, developerMode })
  return (
    <Card title="Menu items" meta="Hidden items stay reachable by address" aria-label="Menu items">
      <ul className="setting-list customize-menu">
        {items.map((i) => {
          const locked = isNavLocked(i)
          // The list is not narrowed by Simple, so say which items the Simple menu leaves out.
          const advancedOnly = simple && i.advanced === true
          return (
            <li key={navId(i)} className="customize-menu-row">
              <span id={`menu-item-${navId(i)}`}>{i.label}</span>
              {locked && <span className="muted">Always shown</span>}
              {advancedOnly && <span className="muted">Advanced menu only</span>}
              <Toggle
                aria-labelledby={`menu-item-${navId(i)}`}
                checked={locked || !hidden.includes(navId(i))}
                disabled={locked}
                onChange={(show) => setHidden(navId(i), !show)}
              />
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
