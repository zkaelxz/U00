/*
 * Settings > Update Baihe: for a git working copy started with start.bat, an admin (at the PC or
 * from away) checks whether a newer version is waiting and, behind a confirm, has Baihe stop and
 * start again on it (services/source_update_service.py). Installed copies and people without
 * system.update don't see the card. After the confirm the page waits for the server to go away and
 * come back, then shows the outcome the server recorded.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api/client'
import { applySourceUpdate, checkSourceUpdate, getSourceUpdate } from '../../api/sourceUpdate'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { useInterval } from '../../hooks/useInterval'
import type { SourceUpdateStatus } from '../../types/update'
import {
  SOURCE_UPDATE_NOT_BACK, SOURCE_UPDATE_RESTARTING, SOURCE_UPDATE_TITLE, sourceCheckLine, sourceReason, sourceVersionLine,
} from './sourceUpdateModel'

const SERVER = { serverText: true } as const
const POLL_MS = 3000
const GIVE_UP_MS = 15 * 60 * 1000

export function SourceUpdateCard() {
  const holds = useHolds('system.update')
  const [status, setStatus] = useState<SourceUpdateStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [notBack, setNotBack] = useState(false)
  const sawDown = useRef(false)
  const since = useRef(0)

  useEffect(() => {
    if (holds) getSourceUpdate().then(setStatus, setError)
  }, [holds])

  const run = useCallback(async (action: () => Promise<SourceUpdateStatus>) => {
    setBusy(true)
    setError(null)
    try {
      setStatus(await action())
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [])

  async function apply() {
    setBusy(true)
    setError(null)
    try {
      await applySourceUpdate()
      sawDown.current = false
      since.current = Date.now()
      setRestarting(true)
    } catch (e) {
      setError(e)
      getSourceUpdate().then(setStatus, () => undefined)
    } finally {
      setBusy(false)
    }
  }

  // Waits for the server to go away and come back; the status after that carries the outcome.
  useInterval(() => {
    api.health().then(
      () => {
        if (!sawDown.current) return
        setRestarting(false)
        getSourceUpdate().then(setStatus, setError)
      },
      () => {
        sawDown.current = true
      },
    )
    if (Date.now() - since.current > GIVE_UP_MS) {
      setRestarting(false)
      setNotBack(true)
    }
  }, restarting ? POLL_MS : null)

  if (!holds || (status && !status.available)) return null
  const reason = status ? sourceReason(status) : null
  return (
    <Card title={SOURCE_UPDATE_TITLE} meta={status ? sourceVersionLine(status) : undefined} aria-label={SOURCE_UPDATE_TITLE}>
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
      {!status ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="settings-form">
          {status.last_result && <p role="status" data-testid="source-update-result">{status.last_result}</p>}
          <p data-testid="source-update-check-line">{sourceCheckLine(status)}</p>
          {status.changes.length > 0 && (
            <ul data-testid="source-update-changes">
              {status.changes.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          )}
          {reason && <p className="muted" data-testid="source-update-reason">{reason}</p>}
          {restarting && <p role="status" data-testid="source-update-restarting">{SOURCE_UPDATE_RESTARTING}</p>}
          {notBack && <p role="status">{SOURCE_UPDATE_NOT_BACK}</p>}
          {!restarting && (
            <div className="settings-actions">
              <button type="button" className={buttonClass('secondary')} disabled={busy}
                onClick={() => run(checkSourceUpdate)}>
                Check for updates
              </button>
              {status.can_apply && (
                <ConfirmButton name={status.latest ?? 'the new version'} label="Update and restart…"
                  confirmLabel="Update and restart Baihe" verb="update" tone="primary" busy={busy}
                  onConfirm={() => void apply()} />
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
