import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SessionState } from '../hooks/useSession'
import { JobsContext } from '../hooks/useJobs'
import type { Route } from '../router'
import { NavDrawer } from './NavDrawer'
import { visibleRailGroups, type NavContext } from './navItems'
import { NavGroups } from './navLinks'

const session: SessionState = {
  status: 'ready',
  me: {
    auth_enabled: false,
    signed_in: true,
    sign_in_configured: false,
    zone: 'local',
    user: { id: 1, email: 'o@example.com', display_name: 'Owner', is_admin: true, is_local_owner: true },
    permissions: ['library.read', 'admin.users.read', 'admin.diagnostics'],
  },
}
const ctx: NavContext = { session, pcMode: 'local', developerMode: false }
const library: Route = { name: 'library' }

const withJobs = (running: number, node: ReturnType<typeof createElement>) =>
  createElement(
    JobsContext.Provider,
    {
      value: {
        jobs: Array.from({ length: running }, (_, i) => ({ id: `j${i}`, status: 'running' })) as never,
        error: null,
        polling: false,
        reload: () => {},
      },
    },
    node,
  )

describe('NavDrawer', () => {
  it('renders a labelled, collapsed Menu button that controls the drawer, with no links until opened', () => {
    const html = renderToStaticMarkup(createElement(NavDrawer, { route: library, context: ctx }))
    expect(html).toContain('aria-label="Menu"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('aria-controls="nav-drawer"')
    expect(html).toContain('<dialog id="nav-drawer"')
    expect(html).not.toContain('<a ')
  })
})

describe('NavGroups', () => {
  it('lists every visible registry item under the registry headings, in order', () => {
    const html = renderToStaticMarkup(withJobs(0, createElement(NavGroups, { route: library, context: ctx })))
    const groups = visibleRailGroups(ctx)
    const labels = groups.flatMap((g) => g.items.map((i) => i.item.label))
    const seen = [...html.matchAll(/<span class="rail-label">([^<]+)<\/span>/g)].map((m) => m[1])
    expect(seen).toEqual(labels)
    for (const g of groups) if (g.heading) expect(html).toContain(`>${g.heading}</h2>`)
  })

  it('marks only the current page and shows the Jobs badge while jobs run', () => {
    const html = renderToStaticMarkup(withJobs(2, createElement(NavGroups, { route: { name: 'jobs' }, context: ctx })))
    expect(html.match(/aria-current="page"/g)).toHaveLength(1)
    expect(html).toMatch(/aria-current="page"[^>]*>.*?<span class="rail-label">Jobs<\/span>/)
    expect(html).toContain('data-testid="rail-jobs-count"')
    expect(renderToStaticMarkup(withJobs(0, createElement(NavGroups, { route: { name: 'jobs' }, context: ctx })))).not.toContain('rail-jobs-count')
  })
})
