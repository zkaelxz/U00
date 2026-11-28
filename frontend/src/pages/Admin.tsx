/*
 * People and access: who can use this PC's library and how it is reached.
 * A list down the left (People, Activity, Remote access, PC profiles) and the one on show on the right. Remote
 * access (setup steps, sign-in settings, the public address check, uploads from away) and PC profiles are PC only.
 * Only an admin (or the owner at the PC) sees it; what is wrong with the PC or the app is Diagnostics, and
 * engines, keys and preferences are Settings.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { FlatPaneContext } from '../components/flatPane'
import { PageShell } from '../components/PageShell'
import { SidePane } from '../components/SidePane'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { Tabs } from '../components/Tabs'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePcProfile } from '../hooks/usePcProfile'
import { usePersistedState } from '../hooks/usePersistedState'
import { useSession } from '../hooks/useSession'
import { AuditLogSection } from './diagnostics/AuditLogSection'
import { UsersSection } from './diagnostics/UsersSection'
import { canViewUsers } from './diagnostics/adminUsers'
import { PcProfileCard } from './settings/PcProfileCard'
import { RemoteSetupSection } from './diagnostics/RemoteSetupSection'
import { RemoteAccessSection } from './settings/RemoteAccessSection'
import { RemoteUploadsCard } from './settings/RemoteUploadsCard'
import { routeHref, useRoute, type AdminCard } from '../router'
import './diagnostics/diagnostics.css'

type AdminPane = 'people' | 'activity' | 'remote' | 'pc-profiles'

// `pcOnly`: everything in the pane renders nothing away from the main PC, so the item is left out there.
const PANES: { id: AdminPane; label: string; pcOnly?: boolean }[] = [
  { id: 'people', label: 'People' },
  { id: 'activity', label: 'Activity' },
  { id: 'remote', label: 'Remote access', pcOnly: true },
  { id: 'pc-profiles', label: 'PC profiles', pcOnly: true },
]

// Older links name the cards that are now parts of Remote access; they still land on their part.
const CARD_PANE: Record<AdminCard, AdminPane> = {
  people: 'people',
  activity: 'activity',
  remote: 'remote',
  address: 'remote',
  uploads: 'remote',
  'pc-profiles': 'pc-profiles',
}

const isPane = (v: unknown): v is AdminPane => PANES.some((p) => p.id === v)
const SHOWING = { showing: true }
const HIDDEN = { showing: false }

export default function AdminPage() {
  const allowed = canViewUsers(useSession())
  return (
    <PageShell width="standard" title="People and access" label="People and access" meta="Who can sign in, what they did, and how to reach this PC from away." className="diag-page">
      {allowed ? <AdminContent /> : (
        <p className="muted">
          Only an admin can see this page. Ask the PC owner to make you an admin.{' '}
          <a href={routeHref({ name: 'library' })}>Back to Library</a>
        </p>
      )}
    </PageShell>
  )
}

function AdminContent() {
  const pc = usePcOnly()
  // PC profiles exist only for the PC window with sign-in off; elsewhere its pane would be empty.
  const profiles = usePcProfile().status !== null
  const route = useRoute()
  const wanted = route.name === 'admin' ? route.card : undefined
  const [stored, setStored] = usePersistedState<AdminPane>('admin.pane', 'people')
  // A link's pane wins over the remembered one from the first render, so the part it names is on screen.
  const [pane, setPane] = useState<AdminPane>(() => (wanted ? CARD_PANE[wanted] : isPane(stored) ? stored : 'people'))
  const select = useCallback((p: AdminPane) => {
    setPane(p)
    setStored(p)
  }, [setStored])
  const [seen, setSeen] = useState<AdminCard | undefined>(wanted)
  // The part a link names, until it has been scrolled to once: switching panes later must not pull focus back.
  const [target, setTarget] = useState<AdminCard | null>(wanted ?? null)
  if (seen !== wanted) {
    setSeen(wanted)
    if (wanted) {
      setPane(CARD_PANE[wanted])
      setTarget(wanted)
    }
  }
  const [count, setCount] = useState<number | undefined>(undefined)

  const shown = PANES.filter((p) => !(p.pcOnly && pc === 'remote') && !(p.id === 'pc-profiles' && !profiles))
  const active = shown.some((p) => p.id === pane) ? pane : 'people'

  // `pc`, `profiles` and `active` are dependencies because the PC-only parts are drawn only once the page knows
  // it is at the PC; the target is kept until its part is on screen.
  useEffect(() => {
    if (!target) return
    const frame = requestAnimationFrame(() => {
      const el = document.getElementById(`admin-card-${target}`)
      const heading = el?.querySelector<HTMLElement>('.section-title')
      if (!el || !heading || el.offsetParent === null) return
      el.scrollIntoView({ block: 'start' })
      heading.focus({ preventScroll: true })
      setTarget(null)
    })
    return () => cancelAnimationFrame(frame)
  }, [target, pc, profiles, active])

  // Panes stay mounted (hidden) so a half-typed field or an armed confirm survives switching.
  const panel = (id: AdminPane, children: ReactNode) => (
    <div key={id} id={tabPanelId('admin', id)} role="tabpanel" aria-labelledby={tabButtonId('admin', id)} className="diag-panel" hidden={active !== id}>
      <FlatPaneContext.Provider value={active === id ? SHOWING : HIDDEN}>{children}</FlatPaneContext.Provider>
    </div>
  )
  // An anchor for deep links; a part that renders nothing leaves no gap.
  const slot = (id: AdminCard, children: ReactNode) => <div id={`admin-card-${id}`} className="admin-card-slot">{children}</div>
  const onCount = useCallback((n: number) => setCount(n), [])

  return (
    <SidePane nav={(
      <Tabs side label="People and access" idPrefix="admin" active={active} onSelect={select}
        tabs={shown.map((p) => ({ id: p.id, label: p.label, count: p.id === 'people' ? count : undefined }))} />
    )}>
      {panel('people', slot('people', <UsersSection pc={pc} onCount={onCount} />))}
      {panel('activity', slot('activity', <AuditLogSection />))}
      {pc !== 'remote' && panel('remote', (
        <>
          {slot('remote', <RemoteSetupSection pc={pc} />)}
          {slot('address', <RemoteAccessSection />)}
          {slot('uploads', <RemoteUploadsCard />)}
        </>
      ))}
      {pc !== 'remote' && profiles && panel('pc-profiles', slot('pc-profiles', <PcProfileCard />))}
    </SidePane>
  )
}
