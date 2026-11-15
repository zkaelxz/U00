/*
 * People and access: who can use this PC's library and how it is reached.
 * Sign-in accounts, the activity log, the public address check, uploads from away and
 * PC profiles (the last two are PC only). Only an
 * admin (or the owner at the PC) sees it; what is wrong with the PC or the
 * app is Diagnostics, and engines, keys and preferences are Settings.
 */
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
import { routeHref } from '../router'
import './diagnostics/diagnostics.css'

export default function AdminPage() {
  const pc = usePcOnly()
  const allowed = canViewUsers(useSession())
  return (
    <PageShell width="narrow" title="People and access" label="People and access" meta="Who can sign in, what they did, and how this PC is reached." className="diag-page">
      {allowed ? (
        // The first card opens the first time; each card then remembers how it was left.
        <div className="diag-folds">
          <UsersSection pc={pc} defaultOpen />
          <AuditLogSection />
          <RemoteAccessSection />
          <CardFoldContext.Provider value={{ storageKey: 'admin.uploads-away', defaultOpen: false }}><RemoteUploadsCard /></CardFoldContext.Provider>
          <CardFoldContext.Provider value={{ storageKey: 'admin.pc-profiles', defaultOpen: false }}><PcProfileCard /></CardFoldContext.Provider>
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
