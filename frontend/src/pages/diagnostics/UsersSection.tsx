import { useCallback, useEffect, useState } from 'react'

import { listAdminUsers, revokeUserAdmin, revokeUserSessions, setUserActive, setUserPermission } from '../../api/adminUsers'
import { removeUserOllamaKey } from '../../api/memberKey'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { HelpTip } from '../../components/HelpTip'
import { Section } from '../../components/Section'
import type { PcMode } from '../../hooks/usePcOnly'
import { useSession } from '../../hooks/useSession'
import type { AdminUser } from '../../types/adminUsers'
import {
  canChangeUsers, canViewUsers, CHANGES_AT_PC, revokeAdminBlock, rowBlocks, sessionsText, showRevokeAdmin,
  rightAbout, rightName, showRights, userName,
} from './adminUsers'
import { useDetailsOpen } from './diagnosticsAdmin'

type Action = 'active' | 'revoke' | 'admin' | 'ollama-key'
type Busy = { id: number; what: Action | 'right' } | null

/**
 * "People": the household's accounts. An admin can deactivate or activate
 * one, end its sessions, remove the Ollama key they saved (never see it), or
 * (on the PC only; not shown elsewhere) remove an admin's rights, each after
 * a confirm step, and give or take a member account's rights (one
 * toggle each, no confirm: each is undone by the same toggle). Admin accounts
 * hold every right and can only be changed on the PC (off elsewhere, with
 * the reason). Adding people stays on the PC (python -m api). Loaded when
 * opened; hidden from anyone without admin.users.read, and read-only (no
 * buttons) without admin.users.
 */
export function UsersSection({ pc, defaultOpen = false }: { pc: PcMode; defaultOpen?: boolean }) {
  const session = useSession()
  if (!canViewUsers(session)) return null
  const signInOff = session.status === 'ready' && !session.me.auth_enabled
  return <UsersBody signInOff={signInOff} pc={pc} canChange={canChangeUsers(session)} defaultOpen={defaultOpen} />
}

function UsersBody({ signInOff, pc, canChange, defaultOpen }: { signInOff: boolean; pc: PcMode; canChange: boolean; defaultOpen: boolean }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [rights, setRights] = useState<string[]>([])
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [note, setNote] = useState('')
  const [openRef, open] = useDetailsOpen()

  // Leaves the error alone: the reload after a refused action must not hide why.
  const load = useCallback(() => {
    listAdminUsers().then((r) => {
      setUsers(r.users)
      setRights(r.member_permissions ?? [])
    }, setError)
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const run = async (user: AdminUser, what: Action) => {
    setBusy({ id: user.id, what })
    setNote('')
    setError(null)
    try {
      if (what === 'ollama-key') {
        await removeUserOllamaKey(user.id)
        setNote(`${user.email}: removed their Ollama key.`)
      } else if (what === 'admin') {
        const r = await revokeUserAdmin(user.id)
        setNote(`${r.email} is no longer an admin and was signed out.`)
      } else if (what === 'revoke') {
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

  const setRight = async (user: AdminUser, permission: string, granted: boolean) => {
    setBusy({ id: user.id, what: 'right' })
    setNote('')
    setError(null)
    try {
      const r = await setUserPermission(user.id, permission, granted)
      setNote(`${r.email}: ${rightName(permission)} ${granted ? 'given' : 'taken away'}.`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      load()
    }
  }

  return (
    <Section title="People" storageKey="admin.people" defaultOpen={defaultOpen} count={users?.length}
      summary="Who can sign in">
      <div ref={openRef} className="diag-stack" data-testid="admin-users">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {signInOff && (
          <p className="muted">Sign-in is off, so these accounts are only used once it is turned on.</p>
        )}
        {!canChange && <p className="muted">{CHANGES_AT_PC}</p>}
        {users === null ? (
          !error && <p className="muted">Loading…</p>
        ) : users.length === 0 ? (
          <p className="muted">No one is signed up yet. Ask the first person to sign in on the main PC.</p>
        ) : (
          <ul className="pkg-list admin-user-list" aria-label="People">
            {users.map((u) => (
              <UserRow key={u.id} user={u} users={users} pc={pc} busy={busy} canChange={canChange}
                rights={rights} onRun={(what) => void run(u, what)}
                onRight={(p, on) => void setRight(u, p, on)} />
            ))}
          </ul>
        )}
        <span className="muted" aria-live="polite">{note}</span>
      </div>
    </Section>
  )
}

function UserRow({ user: u, users, pc, busy, canChange, rights, onRun, onRight }: {
  user: AdminUser
  users: AdminUser[]
  pc: PcMode
  busy: Busy
  canChange: boolean
  rights: string[]
  onRun: (what: Action) => void
  onRight: (permission: string, granted: boolean) => void
}) {
  const { deactivate: offBlock, revoke: sessBlock, activate: onBlock, key: keyBlock } = rowBlocks(u, users, pc)
  const demote = canChange && showRevokeAdmin(u, pc)
  const adminBlock = demote ? revokeAdminBlock(u, users) : null
  const mine = busy?.id === u.id
  const why = canChange
    ? [...new Set([offBlock, sessBlock, onBlock, u.has_own_ollama_key ? keyBlock : null, adminBlock].filter(Boolean))].join(' ')
    : ''
  const whyId = `admin-user-why-${u.id}`
  return (
    <li>
      <div className="admin-user-main">
        <strong>{userName(u)}</strong>{' '}
        {u.is_admin && <Badge tone="info">Admin</Badge>}{' '}
        {!u.is_active && <Badge tone="warn">Deactivated</Badge>}{' '}
        {u.is_self && <Badge>You</Badge>}{' '}
        {u.has_own_ollama_key && <Badge tone="neutral">Own Ollama key</Badge>}
        <div className="muted">
          {[sessionsText(u.active_sessions), u.has_google_binding ? 'Google account linked' : 'Has not signed in yet']
            .join(' · ')}
        </div>
        {why && <div className="muted" id={whyId}>{why}</div>}
      </div>
      {canChange && <div className="actions">
        <ConfirmButton name={u.email} label="Sign out everywhere…" verb="sign out" tone="primary"
          busy={mine && busy?.what === 'revoke'} disabled={busy !== null || !!sessBlock}
          describedBy={sessBlock ? whyId : undefined} onConfirm={() => onRun('revoke')} />
        {u.is_active ? (
          <ConfirmButton name={u.email} label="Deactivate…" verb="deactivate"
            busy={mine && busy?.what === 'active'} disabled={busy !== null || !!offBlock}
            describedBy={offBlock ? whyId : undefined} onConfirm={() => onRun('active')} />
        ) : (
          <ConfirmButton name={u.email} label="Activate…" verb="activate" tone="primary"
            busy={mine && busy?.what === 'active'} disabled={busy !== null || !!onBlock}
            describedBy={onBlock ? whyId : undefined} onConfirm={() => onRun('active')} />
        )}
        {u.has_own_ollama_key && (
          <ConfirmButton name={u.email} label="Remove Ollama key…" verb="remove the Ollama key of"
            busy={mine && busy?.what === 'ollama-key'} disabled={busy !== null || !!keyBlock}
            describedBy={keyBlock ? whyId : undefined} onConfirm={() => onRun('ollama-key')} />
        )}
        {demote && (
          <ConfirmButton name={u.email} label="Remove admin…" verb="remove admin rights from"
            busy={mine && busy?.what === 'admin'} disabled={busy !== null || !!adminBlock}
            describedBy={adminBlock ? whyId : undefined} onConfirm={() => onRun('admin')} />
        )}
      </div>}
      {showRights(u) && rights.length > 0 && (
        <fieldset className="admin-user-rights" aria-busy={mine && busy?.what === 'right'}>
          <legend>Rights</legend>
          <ul>
            {rights.map((p) => {
              const about = rightAbout(p)
              const on = u.permissions.includes(p)
              return (
                <li key={p} className={on ? 'on' : undefined}>
                  <label>
                    <input type="checkbox" checked={on} disabled={!canChange || busy !== null}
                      onChange={(e) => onRight(p, e.target.checked)} />
                    <span>
                      <span className="admin-right-name">{rightName(p)}</span>
                      <code className="admin-right-id">{p}</code>
                    </span>
                  </label>
                  {about && <HelpTip label={rightName(p)} id={`admin-right-${u.id}-${p}`}>{about}</HelpTip>}
                </li>
              )
            })}
          </ul>
        </fieldset>
      )}
    </li>
  )
}
