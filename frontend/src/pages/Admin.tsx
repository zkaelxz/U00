/*
 * People and access: who can use this PC's library and how it is reached.
 * Sign-in accounts, the activity log, the public address check, uploads from away and
 * PC profiles (the last two are PC only). Only an
 * admin (or the owner at the PC) sees it; what is wrong with the PC or the
 * app is Diagnostics, and engines, keys and preferences are Settings.
 */
import { useEffect, useState, type ReactNode } from 'react'

import { PageShell } from '../components/PageShell'
import { usePcOnly } from '../hooks/usePcOnly'
import { useSession } from '../hooks/useSession'
import { AuditLogSection } from './diagnostics/AuditLogSection'
import { UsersSection } from './diagnostics/UsersSection'
import { canViewUsers } from './diagnostics/adminUsers'
import { CardFoldContext } from '../components/cardFold'
import { PcProfileCard } from './settings/PcProfileCard'
import { RemoteAccessSection } from './settings/RemoteAccessSection'
import { RemoteUploadsCard } from './settings/RemoteUploadsCard'
import { routeHref, useRoute, type AdminCard } from '../router'
import './diagnostics/diagnostics.css'

export default function AdminPage() {
  const pc = usePcOnly()
  const allowed = canViewUsers(useSession())
  const route = useRoute()
  const wanted = route.name === 'admin' ? route.card : undefined
  // A link that names a card ("#/admin?card=uploads") opens it and brings it into view; each number only goes up.
  const [opens, setOpens] = useState<Partial<Record<AdminCard, number>>>({})
  useEffect(() => {
    if (!wanted) return
    setOpens((cur) => ({ ...cur, [wanted]: (cur[wanted] ?? 0) + 1 }))
    document.getElementById(`admin-card-${wanted}`)?.scrollIntoView({ block: 'start' })
  }, [wanted])
  const slot = (id: AdminCard, children: ReactNode) => <div id={`admin-card-${id}`} className="admin-card-slot">{children}</div>
  return (
    <PageShell width="narrow" title="People and access" label="People and access" meta="Who can sign in, what they did, and how this PC is reached." className="diag-page">
      {allowed ? (
        // The first card opens the first time; each card then remembers how it was left.
        <div className="diag-folds">
          {slot('people', <UsersSection pc={pc} defaultOpen openSignal={opens.people} />)}
          {slot('activity', <AuditLogSection openSignal={opens.activity} />)}
          {slot('address', <RemoteAccessSection openSignal={opens.address} />)}
          {slot('uploads', (
            <CardFoldContext.Provider value={{ storageKey: 'admin.uploads-away', defaultOpen: false, openSignal: opens.uploads }}><RemoteUploadsCard /></CardFoldContext.Provider>
          ))}
          {slot('pc-profiles', (
            <CardFoldContext.Provider value={{ storageKey: 'admin.pc-profiles', defaultOpen: false, openSignal: opens['pc-profiles'] }}><PcProfileCard /></CardFoldContext.Provider>
          ))}
        </div>
      ) : (
        <p className="muted">
          Only an admin can see this page. Ask the PC owner.{' '}
          <a href={routeHref({ name: 'library' })}>Back to Library</a>
        </p>
      )}
    </PageShell>
  )
}
