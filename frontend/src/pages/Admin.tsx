/*
 * People and access: who can use this PC's library and how it is reached.
 * Sign-in accounts, the activity log, the remote access setup steps, the public address check,
 * uploads from away and PC profiles (the last two are PC only). Only an
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
import { RemoteSetupSection } from './diagnostics/RemoteSetupSection'
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
  const [seen, setSeen] = useState<{ card?: AdminCard; opens: Partial<Record<AdminCard, number>> }>({ opens: {} })
  if (seen.card !== wanted) {
    setSeen({ card: wanted, opens: wanted ? { ...seen.opens, [wanted]: (seen.opens[wanted] ?? 0) + 1 } : seen.opens })
  }
  const opens = seen.opens
  // `pc` is a dependency because the PC-only cards are drawn only once the page knows it is at the PC.
  useEffect(() => {
    if (wanted) document.getElementById(`admin-card-${wanted}`)?.scrollIntoView({ block: 'start' })
  }, [wanted, pc])
  const slot = (id: AdminCard, children: ReactNode) => <div id={`admin-card-${id}`} className="admin-card-slot">{children}</div>
  return (
    <PageShell width="narrow" title="People and access" label="People and access" meta="Who can sign in, what they did, and how to reach this PC from away." className="diag-page">
      {allowed ? (
        // The first card opens the first time; each card then remembers how it was left.
        <div className="diag-folds">
          {slot('people', <UsersSection pc={pc} defaultOpen openSignal={opens.people} />)}
          {slot('activity', <AuditLogSection openSignal={opens.activity} />)}
          <RemoteSetupSection pc={pc} />
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
          Only an admin can see this page. Ask the PC owner to make you an admin.{' '}
          <a href={routeHref({ name: 'library' })}>Back to Library</a>
        </p>
      )}
    </PageShell>
  )
}
