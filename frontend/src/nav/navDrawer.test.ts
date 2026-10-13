import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SessionState } from '../hooks/useSession'
import { JobsContext } from '../hooks/useJobs'
import type { Route } from '../router'
import { MenuButton, NavDrawer } from './NavDrawer'
import { PhoneBar } from './PhoneBar'
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
  it('renders a collapsed Menu button that controls the drawer, and the drawer with no links until opened', () => {
    const button = renderToStaticMarkup(createElement(MenuButton, { open: false, onOpen: () => {} }))
    expect(button).toContain('aria-label="Menu"')
    expect(button).toContain('aria-expanded="false"')
    expect(button).toContain('aria-controls="nav-drawer"')
    const html = renderToStaticMarkup(createElement(NavDrawer, { route: library, context: ctx, open: false, onOpenChange: () => {} }))
    expect(html).toContain('<dialog id="nav-drawer"')
    expect(html).not.toContain('<a ')
  })
})

describe('PhoneBar', () => {
  const bar = (running: number, route: Route = library, moreOpen = false, context: NavContext = owner) =>
    renderToStaticMarkup(withJobs(running, createElement(PhoneBar, { route, context, moreOpen, onMore: () => {} })))
  // Make subtitles needs admin.library; the bar leaves out what the person may not use.
  const owner: NavContext = { ...ctx, session: { ...session, me: { ...session.me, permissions: [...session.me.permissions, 'admin.library'] } } as SessionState }

  it('has Library, Make, Jobs in that order and a More button that controls the drawer', () => {
    const html = bar(0)
    const labels = [...html.matchAll(/<span>([^<]+)<\/span>/g)].map((m) => m[1])
    expect(labels).toEqual(['Library', 'Make', 'Jobs', 'More'])
    expect(html).toContain('aria-label="More"')
    expect(html).toContain('aria-controls="nav-drawer"')
    expect(bar(0, library, true)).toContain('aria-expanded="true"')
  })

  it('leaves out an item the person may not use', () => {
    const labels = [...bar(0, library, false, ctx).matchAll(/<span>([^<]+)<\/span>/g)].map((m) => m[1])
    expect(labels).toEqual(['Library', 'Jobs', 'More'])
  })

  it('marks only the current page and shows the Jobs badge while jobs run', () => {
    const html = bar(2, { name: 'jobs' })
    expect(html.match(/aria-current="page"/g)).toHaveLength(1)
    expect(html).toContain('data-testid="phone-bar-jobs-count"')
    expect(bar(0, { name: 'jobs' })).not.toContain('phone-bar-jobs-count')
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
