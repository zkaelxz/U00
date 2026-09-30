import { useCallback, useEffect, useState } from 'react'

import { listAdminUsers, revokeUserSessions, setUserActive } from '../../api/adminUsers'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { useSession } from '../../hooks/useSession'
import type { AdminUser } from '../../types/adminUsers'
import { canManageUsers, deactivateBlock, revokeBlock, sessionsText, userName } from './adminUsers'
import { useDetailsOpen } from './diagnosticsAdmin'

type Busy = { id: number; what: 'active' | 'revoke' } | null

/**
 * "Users": the household's accounts. An admin can deactivate or activate
 * one and end its sessions, each after a confirm step. Adding people and
 * changing permissions stay on the PC (python -m api). Loaded when opened;
 * hidden from anyone without admin.users.
 */
export function UsersSection() {
  const session = useSession()
  if (!canManageUsers(session)) return null
  const signInOff = session.status === 'ready' && !session.me.auth_enabled
  return <UsersBody signInOff={signInOff} />
}

function UsersBody({ signInOff }: { signInOff: boolean }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [note, setNote] = useState('')
  const [openRef, open] = useDetailsOpen()

  // Leaves the error alone: the reload after a refused action must not hide why.
  const load = useCallback(() => {
    listAdminUsers().then((r) => setUsers(r.users), setError)
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const run = async (user: AdminUser, what: 'active' | 'revoke') => {
    setBusy({ id: user.id, what })
    setNote('')
    setError(null)
    try {
      if (what === 'revoke') {
        const r = await revokeUserSessions(user.id)
        setNote(`${user.email}: ended ${r.revoked} ${r.revoked === 1 ? 'session' : 'sessions'}.`)
      } else {
        const r = await setUserActive(user.id, !user.is_active)
        setNote(`${r.email} is ${r.is_active ? 'active again' : 'deactivated and signed out'}.`)
      }
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      load()
    }
  }

  return (
    <Section title="Users" storageKey="diagnostics.users" count={users?.length}
      summary="Who can sign in">
      <div ref={openRef} className="diag-stack" data-testid="admin-users">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {signInOff && (
          <p className="muted">Sign-in is off, so these accounts are only used once it is turned on.</p>
        )}
        {users === null ? (
          !error && <p className="muted">Loading…</p>
        ) : users.length === 0 ? (
          <p className="muted">No users yet. Add the first admin on the PC with: python -m api grant-admin &lt;email&gt;</p>
        ) : (
          <ul className="pkg-list admin-user-list" aria-label="Users">
            {users.map((u) => (
              <UserRow key={u.id} user={u} users={users} busy={busy} onRun={(what) => void run(u, what)} />
            ))}
          </ul>
        )}
        <span className="muted" aria-live="polite">{note}</span>
      </div>
    </Section>
  )
}

function UserRow({ user: u, users, busy, onRun }: {
  user: AdminUser
  users: AdminUser[]
  busy: Busy
  onRun: (what: 'active' | 'revoke') => void
}) {
  const offBlock = deactivateBlock(u, users)
  const sessBlock = revokeBlock(u)
  const mine = busy?.id === u.id
  const why = [offBlock, sessBlock].filter(Boolean).join(' ')
  const whyId = `admin-user-why-${u.id}`
  return (
    <li>
      <div className="admin-user-main">
        <strong>{userName(u)}</strong>{' '}
        {u.is_admin && <Badge tone="info">Admin</Badge>}{' '}
        {!u.is_active && <Badge tone="warn">Deactivated</Badge>}{' '}
        {u.is_self && <Badge>You</Badge>}
        <div className="muted">
          {[sessionsText(u.active_sessions), u.has_google_binding ? 'Google account linked' : 'has not signed in yet']
            .join(' · ')}
        </div>
        {why && <div className="muted" id={whyId}>{why}</div>}
      </div>
      <div className="actions">
        <ConfirmButton name={u.email} label="Sign out everywhere…" verb="sign out" tone="primary"
          busy={mine && busy?.what === 'revoke'} disabled={busy !== null || !!sessBlock}
          describedBy={sessBlock ? whyId : undefined} onConfirm={() => onRun('revoke')} />
        {u.is_active ? (
          <ConfirmButton name={u.email} label="Deactivate…" verb="deactivate"
            busy={mine && busy?.what === 'active'} disabled={busy !== null || !!offBlock}
            describedBy={offBlock ? whyId : undefined} onConfirm={() => onRun('active')} />
        ) : (
          <ConfirmButton name={u.email} label="Activate…" verb="activate" tone="primary"
            busy={mine && busy?.what === 'active'} disabled={busy !== null}
            onConfirm={() => onRun('active')} />
        )}
      </div>
    </li>
  )
}
