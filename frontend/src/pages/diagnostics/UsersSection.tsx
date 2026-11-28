import { useCallback, useEffect, useState } from 'react'

import {
  addAdminUser, deleteAdminUser, listAdminUsers, revokeUserAdmin, revokeUserSessions, setUserActive, setUserDisplayName, setUserJobLimit, setUserPaidLimit, setUserPermission,
} from '../../api/adminUsers'
import { removeUserOllamaKey } from '../../api/memberKey'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { HelpTip } from '../../components/HelpTip'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { WarnBar } from '../../components/WarnBar'
import type { PcMode } from '../../hooks/usePcOnly'
import { useSession } from '../../hooks/useSession'
import type { AdminUser } from '../../types/adminUsers'
import {
  canChangeUsers, canViewUsers, CHANGES_AT_PC, FIND_PEOPLE_AT, MAX_DISPLAY_NAME_CHARS, MAX_EMAIL_CHARS, matchesPerson, paidSpendText, personStatus,
  revokeAdminBlock, jobsTodayText, lastSeenText, rowBlocks, showDeleteUser, showRevokeAdmin, rightAbout, rightName, showRights,
} from './adminUsers'
import { MemberDevices } from './MemberDevices'
import { PersonFields } from './PeopleFields'
import { RightsGrid } from './RightsGrid'
import { useDetailsOpen } from './diagnosticsAdmin'
import { buttonClass } from '../../components/uiClasses'

type Action = 'active' | 'revoke' | 'admin' | 'ollama-key' | 'delete'
type Busy = { id: number; what: Action } | null

/**
 * "People": the household's accounts, one quiet row each that opens on click. An admin can deactivate or
 * activate one, end its sessions, remove the Ollama key they saved (never see it), delete a deactivated member
 * account, or (on the PC only; not shown elsewhere) remove an admin's rights, each after a confirm step.
 * The name and limit fields save themselves (PeopleFields), and a member's rights are switches in one grid
 * below the list (RightsGrid). Admin accounts hold every right and can only be changed on the PC (off
 * elsewhere, with the reason). "Add person" is shown on the main PC only, where the server allows it. Loaded
 * when its pane shows; hidden from anyone without admin.users.read, and read-only without admin.users.
 */
export function UsersSection({ pc, onCount }: { pc: PcMode; onCount?: (n: number) => void }) {
  const session = useSession()
  if (!canViewUsers(session)) return null
  const signInOff = session.status === 'ready' && !session.me.auth_enabled
  return <UsersBody signInOff={signInOff} pc={pc} canChange={canChangeUsers(session)} onCount={onCount} />
}

function UsersBody({ signInOff, pc, canChange, onCount }: { signInOff: boolean; pc: PcMode; canChange: boolean; onCount?: (n: number) => void }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [rights, setRights] = useState<string[]>([])
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [pendingRight, setPendingRight] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [adding, setAdding] = useState(false)
  const [find, setFind] = useState('')
  const [openRef, open] = useDetailsOpen()

  // Leaves the error alone: the reload after a refused action must not hide why.
  const load = useCallback(() => {
    listAdminUsers().then((r) => {
      setUsers(r.users)
      setRights(r.member_permissions ?? [])
      onCount?.(r.users.length)
    }, setError)
  }, [onCount])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  // The server's answer is the person as saved, so the row shows it at once; the reload then refreshes the rest.
  const confirmed = (r: AdminUser) => setUsers((us) => us && us.map((x) => (x.id === r.id ? { ...x, ...r } : x)))

  const run = async (user: AdminUser, what: Action) => {
    setBusy({ id: user.id, what })
    setNote('')
    setError(null)
    try {
      if (what === 'delete') {
        await deleteAdminUser(user.id)
        setNote(`${user.email} was deleted. They can be added again as a new person.`)
      } else if (what === 'ollama-key') {
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
    setPendingRight(`${user.id}:${permission}`)
    setNote('')
    setError(null)
    try {
      const r = await setUserPermission(user.id, permission, granted)
      confirmed(r)
      setNote(`${r.email}: ${rightName(permission)} ${granted ? 'given' : 'taken away'}.`)
    } catch (e) {
      setError(e)
    } finally {
      setPendingRight(null)
      load()
    }
  }

  // The field shows the result itself, so these only refresh the list on success and pass a refusal back.
  const saveWith = (call: () => Promise<AdminUser>) => call().then((r) => {
    confirmed(r)
    load()
  })

  const members = users?.filter(showRights) ?? []
  const shown = users?.filter((u) => matchesPerson(u, find)) ?? []
  return (
    <Section title="People" count={users?.length}>
      <div ref={openRef} className="diag-stack" data-testid="admin-users">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {signInOff && <WarnBar>Sign-in is off, so these accounts are only used once it is turned on.</WarnBar>}
        {!canChange && <WarnBar>{CHANGES_AT_PC}</WarnBar>}
        {users !== null && (users.length >= FIND_PEOPLE_AT || (canChange && pc === 'local')) && (
          <div className="admin-people-bar">
            {users.length >= FIND_PEOPLE_AT && (
              <input type="search" className="admin-people-find" aria-label="Find a person" placeholder="Find a person"
                value={find} onChange={(e) => setFind(e.target.value)} />
            )}
            {canChange && pc === 'local' && (
              <button type="button" className={buttonClass('secondary', 'sm')} aria-expanded={adding}
                onClick={() => setAdding((a) => !a)}>+ Add person</button>
            )}
          </div>
        )}
        {adding && canChange && pc === 'local' && users !== null && (
          <AddPersonForm busy={busy !== null} onAdded={(u) => {
            setError(null)
            setAdding(false)
            setNote(`${u.email} was added. Their first Google sign-in links the account.`)
            load()
          }} />
        )}
        {users === null ? (
          !error && <p className="muted">Loading…</p>
        ) : users.length === 0 ? (
          <p className="muted">No one added yet. Use Add person to add someone by their Google email; then they can sign in with that Google account.</p>
        ) : shown.length === 0 ? (
          <p className="muted">No one matches “{find.trim()}”.</p>
        ) : (
          <ul className="admin-people" aria-label="People">
            {shown.map((u) => (
              <PersonRow key={u.id} user={u} users={users} pc={pc} busy={busy} canChange={canChange}
                onRun={(what) => void run(u, what)}
                onName={(name) => saveWith(() => setUserDisplayName(u.id, name))}
                onPaidLimit={(usd) => saveWith(() => setUserPaidLimit(u.id, usd))}
                onJobLimit={(jobs) => saveWith(() => setUserJobLimit(u.id, jobs))} />
            ))}
          </ul>
        )}
        <span className="muted" aria-live="polite">{note}</span>
        <RightsGrid members={members.filter((u) => matchesPerson(u, find))} rights={rights} canChange={canChange}
          pending={pendingRight ?? (busy ? 'row' : null)} onRight={(u, p, on) => void setRight(u, p, on)} />
      </div>
    </Section>
  )
}

function PersonRow({ user: u, users, pc, busy, canChange, onRun, onName, onPaidLimit, onJobLimit }: {
  user: AdminUser
  users: AdminUser[]
  pc: PcMode
  busy: Busy
  canChange: boolean
  onRun: (what: Action) => void
  onName: (name: string) => Promise<unknown>
  onPaidLimit: (usd: number | null) => Promise<unknown>
  onJobLimit: (jobs: number | null) => Promise<unknown>
}) {
  // Fixed at first render: the clock is impure, and "seen 5 minutes ago" needs no live tick.
  const [now] = useState(() => Date.now())
  const { deactivate: offBlock, revoke: sessBlock, activate: onBlock, key: keyBlock } = rowBlocks(u, users, pc)
  const demote = canChange && showRevokeAdmin(u, pc)
  const adminBlock = demote ? revokeAdminBlock(u, users) : null
  const mine = busy?.id === u.id
  const why = canChange
    ? [...new Set([offBlock, sessBlock, onBlock, u.has_own_ollama_key ? keyBlock : null, adminBlock].filter(Boolean))].join(' ')
    : ''
  const whyId = `admin-user-why-${u.id}`
  const quiet = buttonClass('ghost', 'sm')
  return (
    <li>
      <details className="admin-person">
        <summary className="admin-person-row">
          <span className="admin-person-who">
            <strong>{u.display_name || u.email}</strong>
            {u.display_name && <span className="muted"> · {u.email}</span>}
            {u.is_admin && <> <Badge tone="info">Admin</Badge></>}
            {!u.is_active && <> <Badge tone="warn">Deactivated</Badge></>}
            {u.is_self && <> <Badge>You</Badge></>}
          </span>
          <span className="muted admin-person-status">{personStatus(u)}</span>
        </summary>
        <div className="admin-person-body">
          <div className="muted">
            {[lastSeenText(u, now), u.has_google_binding ? 'Google account linked' : 'Has not signed in yet', u.has_own_ollama_key ? 'Own Ollama key saved' : null]
              .filter(Boolean).join(' · ')}
          </div>
          <div className="muted">{paidSpendText(u)}</div>
          {showRights(u) && <div className="muted">{jobsTodayText(u)}</div>}
          {why && <div className="muted" id={whyId}>{why}</div>}
          {canChange && <PersonFields user={u} limits={showRights(u)} onName={onName} onPaidLimit={onPaidLimit} onJobLimit={onJobLimit} />}
          {canChange && <div className="actions">
            <ConfirmButton name={u.email} label="Sign out everywhere…" verb="sign out" tone="primary" firstClass={quiet}
              busy={mine && busy?.what === 'revoke'} disabled={busy !== null || !!sessBlock}
              describedBy={sessBlock ? whyId : undefined} onConfirm={() => onRun('revoke')} />
            {u.is_active ? (
              <ConfirmButton name={u.email} label="Deactivate…" verb="deactivate" firstClass={`${quiet} admin-danger-link`}
                busy={mine && busy?.what === 'active'} disabled={busy !== null || !!offBlock}
                describedBy={offBlock ? whyId : undefined} onConfirm={() => onRun('active')} />
            ) : (
              <ConfirmButton name={u.email} label="Activate…" verb="activate" tone="primary" firstClass={quiet}
                busy={mine && busy?.what === 'active'} disabled={busy !== null || !!onBlock}
                describedBy={onBlock ? whyId : undefined} onConfirm={() => onRun('active')} />
            )}
            {u.has_own_ollama_key && (
              <ConfirmButton name={u.email} label="Remove Ollama key…" verb="remove the Ollama key of" firstClass={quiet}
                busy={mine && busy?.what === 'ollama-key'} disabled={busy !== null || !!keyBlock}
                describedBy={keyBlock ? whyId : undefined} onConfirm={() => onRun('ollama-key')} />
            )}
            {showDeleteUser(u) && (
              <ConfirmButton name={u.email} label="Delete…" verb="delete" firstClass={`${quiet} admin-danger-link`}
                busy={mine && busy?.what === 'delete'} disabled={busy !== null}
                onConfirm={() => onRun('delete')} />
            )}
            {demote && (
              <ConfirmButton name={u.email} label="Remove admin…" verb="remove admin rights from" firstClass={`${quiet} admin-danger-link`}
                busy={mine && busy?.what === 'admin'} disabled={busy !== null || !!adminBlock}
                describedBy={adminBlock ? whyId : undefined} onConfirm={() => onRun('admin')} />
            )}
          </div>}
          {canChange && u.active_sessions > 0 && <MemberDevices userId={u.id} email={u.email} block={sessBlock} />}
        </div>
      </details>
    </li>
  )
}

/** Allowlists an email with the household rights; `python -m api add-user` from the page. */
function AddPersonForm({ busy, onAdded }: { busy: boolean; onAdded: (user: AdminUser) => void }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [extension, setExtension] = useState(false)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const submit = async () => {
    setAdding(true)
    setError(null)
    try {
      const u = await addAdminUser({ email: email.trim(), display_name: name.trim(), extension_send: extension })
      setEmail('')
      setName('')
      setExtension(false)
      onAdded(u)
    } catch (e) {
      setError(e)
    } finally {
      setAdding(false)
    }
  }
  return (
    <form className="admin-add-user" aria-label="Add a person" onSubmit={(e) => {
      e.preventDefault()
      if (email.trim() && !adding) void submit()
    }}>
      <div className="field-label-row">
        <h3 className="subhead">Add a person</h3>
        <HelpTip label="Add a person" id="admin-add-user-help">
          They sign in with Google using this email. They start with the household rights (see the library, read and
          edit subtitles, start and stop jobs, use Review, play audio and video, add their own titles, and import
          chapters from novel and comic sites); switch on more in Rights once they are added.
        </HelpTip>
      </div>
      {/* Left visible: without it the person's first sign-in fails before it reaches Baihe. */}
      <p className="muted">
        While your Google app is in testing, also add the same email to its test users (Google Cloud Console, OAuth
        consent screen), or Google refuses the sign-in before it reaches Baihe.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Field label="Email">
        <input type="email" required autoComplete="off" spellCheck={false} maxLength={MAX_EMAIL_CHARS}
          value={email} disabled={adding} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Name (optional)">
        <input type="text" autoComplete="off" maxLength={MAX_DISPLAY_NAME_CHARS}
          value={name} disabled={adding} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Also allow the browser extension and Baihe Grab" help={rightAbout('extension.send') ?? undefined}>
        <Toggle checked={extension} disabled={adding} onChange={setExtension} />
      </Field>
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={busy || adding || !email.trim()}>
          {adding ? 'Adding…' : 'Add person'}
        </button>
      </div>
    </form>
  )
}
