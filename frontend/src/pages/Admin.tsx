/*
 * People and access: who can use this PC's library and how it is reached.
 * Sign-in accounts, the activity log and the public address check. Only an
 * admin (or the owner at the PC) sees it; what is wrong with the PC or the
 * app is Diagnostics, and engines, keys and preferences are Settings.
 */
import { usePcOnly } from '../hooks/usePcOnly'
import { useSession } from '../hooks/useSession'
import { AuditLogSection } from './diagnostics/AuditLogSection'
import { UsersSection } from './diagnostics/UsersSection'
import { canViewUsers } from './diagnostics/adminUsers'
import { RemoteAccessSection } from './settings/RemoteAccessSection'
import './diagnostics/diagnostics.css'

export default function AdminPage() {
  const pc = usePcOnly()
  const allowed = canViewUsers(useSession())
  return (
    <section className="page-narrow diag-page" aria-label="People and access">
      <header className="page-head">
        <h2>People and access</h2>
        <p className="page-meta">Who can sign in, what they did, and the public address check.</p>
      </header>
      {allowed ? (
        // The first card opens the first time; each card then remembers how it was left.
        <div className="diag-folds">
          <UsersSection pc={pc} defaultOpen />
          <AuditLogSection />
          <RemoteAccessSection />
        </div>
      ) : (
        <p className="muted">Only an admin can see this page.</p>
      )}
    </section>
  )
}
