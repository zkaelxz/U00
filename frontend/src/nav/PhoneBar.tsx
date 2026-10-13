/*
 * Bottom tab bar for screens narrower than 600px, outside title workspaces and readers (those keep the header's
 * Menu button, since their bottom edge belongs to their own actions). Three destinations that the registry
 * and the person's permissions allow, and More, which opens the drawer with everything else.
 */
import { activeCount, badgeText } from '../components/jobsMenuState'
import { useJobs } from '../hooks/useJobs'
import { routeHref, type Route } from '../router'
import { NAV_DRAWER_ID } from './NavDrawer'
import { visibleRailGroups, type NavContext } from './navItems'
import { Icon, navIconPath } from './navLinks'
import './phoneBar.css'

const BAR_LABELS = ['Library', 'Make subtitles', 'Jobs']
const MORE_ICON = 'M5 12h.01M12 12h.01M19 12h.01'

export function PhoneBar({ route, context, moreOpen, onMore }: { route: Route; context: NavContext; moreOpen: boolean; onMore: () => void }) {
  const running = activeCount(useJobs().jobs ?? [])
  const items = visibleRailGroups(context)
    .flatMap((g) => g.items)
    .filter(({ item }) => BAR_LABELS.includes(item.label))
    .sort((a, b) => BAR_LABELS.indexOf(a.item.label) - BAR_LABELS.indexOf(b.item.label))
  return (
    <nav className="phone-bar" aria-label="Quick">
      {items.map(({ item, active }) => (
        <a key={item.label} href={routeHref(item.target)} aria-current={active.includes(route.name) ? 'page' : undefined}>
          <Icon d={navIconPath(item.label)} />
          <span>{item.label === 'Make subtitles' ? 'Make' : item.label}</span>
          {item.badge === 'running-jobs' && running > 0 && (
            <span className="phone-bar-badge" data-testid="phone-bar-jobs-count">{badgeText(running)}</span>
          )}
        </a>
      ))}
      <button type="button" aria-label="More" aria-expanded={moreOpen} aria-controls={NAV_DRAWER_ID} onClick={onMore}>
        <Icon d={MORE_ICON} />
        <span>More</span>
      </button>
    </nav>
  )
}
