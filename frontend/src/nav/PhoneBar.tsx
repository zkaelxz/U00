/*
 * Bottom tab bar for screens narrower than 600px, outside title workspaces and readers (those keep the header's
 * Menu button, since their bottom edge belongs to their own actions), except the Simple check page, which asks for it
 * and keeps its own bar just above it (nav/phoneBarRequest.ts). Three destinations that the registry
 * and the person's permissions allow, and More, which opens the drawer with everything else.
 */
import { activeCount, badgeText } from '../components/jobsMenuState'
import { useJobs } from '../hooks/useJobs'
import { usePublishedHeight } from '../hooks/usePublishedHeight'
import { routeHref, type Route } from '../router'
import { NAV_DRAWER_ID } from './NavDrawer'
import { visibleRailGroups, type NavContext } from './navItems'
import { navIconPath } from './navIcons'
import { Icon } from './navLinks'
import './phoneBar.css'

const BAR_LABELS = ['Library', 'Find & Add', 'Jobs']
const MORE_ICON = 'M5 12h.01M12 12h.01M19 12h.01'

export function PhoneBar({ route, context, moreOpen, onMore, words = false }: { route: Route; context: NavContext; moreOpen: boolean; onMore: () => void; words?: boolean }) {
  const running = activeCount(useJobs().jobs ?? [])
  // A page with its own bottom bar leaves this much room under it.
  const barRef = usePublishedHeight('--phone-bar-h')
  const items = visibleRailGroups(context)
    .flatMap((g) => g.items)
    .filter(({ item }) => BAR_LABELS.includes(item.label))
    .sort((a, b) => BAR_LABELS.indexOf(a.item.label) - BAR_LABELS.indexOf(b.item.label))
  return (
    <nav ref={barRef} className={words ? 'phone-bar is-words' : 'phone-bar'} aria-label="Quick">
      {items.map(({ item, active }) => (
        <a key={item.label} href={routeHref(item.target)} aria-current={active.includes(route.name) ? 'page' : undefined}>
          <Icon d={navIconPath(item.label)} />
          <span>{item.label}</span>
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
