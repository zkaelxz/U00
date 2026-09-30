import { useCallback, useEffect, useRef, useState } from 'react'

import { listAdminUsers, listAudit } from '../../api/adminUsers'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { useSession } from '../../hooks/useSession'
import type { AdminUser, AuditEvent } from '../../types/adminUsers'
import {
  appendAudit, auditActionLabel, auditActor, auditTime, canManageUsers, userName,
} from './adminUsers'
import { useDetailsOpen } from './diagnosticsAdmin'

type Filter = { action: string; userId: number | null }

/**
 * "Audit log": sign-ins and admin actions, newest first, read-only. Loads
 * one page when opened; "Show older" pages back. Hidden from anyone
 * without admin.users.
 */
export function AuditLogSection() {
  const session = useSession()
  if (!canManageUsers(session)) return null
  return <AuditBody />
}

function AuditBody() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [actions, setActions] = useState<string[]>([])
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [filter, setFilter] = useState<Filter>({ action: '', userId: null })
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [openRef, open] = useDetailsOpen()
  // Only the newest request may fill the list (filters can change mid-load).
  const seq = useRef(0)

  const load = useCallback((f: Filter) => {
    const mine = ++seq.current
    listAudit({ action: f.action || null, user_id: f.userId }).then((p) => {
      if (mine !== seq.current) return
      setEvents(p.events)
      setNext(p.next_before_id)
      setActions(p.actions)
      setError(null)
    }, (e: unknown) => {
      if (mine === seq.current) setError(e)
    })
  }, [])

  useEffect(() => {
    if (!open) return
    load(filter)
    listAdminUsers().then((r) => setUsers(r.users), () => undefined)
  }, [open, filter, load])

  const older = async () => {
    if (next == null) return
    const mine = seq.current
    setLoadingMore(true)
    try {
      const p = await listAudit({ before_id: next, action: filter.action || null, user_id: filter.userId })
      if (mine !== seq.current) return
      setEvents((cur) => appendAudit(cur ?? [], p.events))
      setNext(p.next_before_id)
    } catch (e) {
      setError(e)
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <Section title="Audit log" storageKey="diagnostics.audit" summary="Sign-ins and admin actions">
      <div ref={openRef} className="diag-stack" data-testid="audit-log">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <div className="actions audit-filters">
          <label>
            Action{' '}
            <select value={filter.action} onChange={(e) => setFilter((f) => ({ ...f, action: e.target.value }))}>
              <option value="">All actions</option>
              {actions.map((a) => <option key={a} value={a}>{auditActionLabel(a)}</option>)}
            </select>
          </label>
          <label>
            User{' '}
            <select value={filter.userId ?? ''}
              onChange={(e) => setFilter((f) => ({ ...f, userId: e.target.value ? Number(e.target.value) : null }))}>
              <option value="">Everyone</option>
              {(users ?? []).map((u) => <option key={u.id} value={u.id}>{userName(u)}</option>)}
            </select>
          </label>
        </div>
        {events === null ? (
          !error && <p className="muted">Loading…</p>
        ) : events.length === 0 ? (
          <p className="muted">Nothing recorded{filter.action || filter.userId != null ? ' for this filter' : ''}.</p>
        ) : (
          <div className="table-scroll">
            <table aria-label="Audit log">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>What</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>{auditTime(e.ts)}</td>
                    <td>{auditActor(e.user_id, users)}</td>
                    <td title={e.action}>{auditActionLabel(e.action)}</td>
                    <td className="audit-detail">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {next != null && events && events.length > 0 && (
          <div className="actions">
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={loadingMore}
              aria-busy={loadingMore || undefined} onClick={() => void older()}>
              {loadingMore ? 'Loading…' : 'Show older'}
            </button>
          </div>
        )}
      </div>
    </Section>
  )
}
