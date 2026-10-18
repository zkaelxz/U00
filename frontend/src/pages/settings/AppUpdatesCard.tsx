/*
 * Settings > App updates: the installed version, "Check for updates",
 * "Download" (checked against the release's SHA-256 and, when this copy has
 * a release key, its signature) and "Install and
 * restart…" behind a confirm, which opens the installer's own Setup. When the
 * release only changes the app folder, "Update app only" downloads the signed
 * app zip and swaps it in (installer/app_swap.py), going back on failure. Nothing
 * downloads or installs without a click; the once-a-day check is off by
 * default. PC only: away from the PC the Card shows the "Run this on the
 * main PC." note, and nothing is fetched until /api/meta has said "local".
 */
import { useCallback, useEffect, useState } from 'react'

import {
  applyAppUpdate, checkForUpdates, downloadAppUpdate, downloadUpdate, getUpdateStatus, installUpdate, setUpdateAutoCheck,
} from '../../api/update'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly, usePcPendingNote } from '../../hooks/usePcOnly'
import type { UpdateStatus } from '../../types/update'
import {
  APP_APPLY_NOTE, APP_RESTARTING_LINE, AUTO_CHECK_HELP, CUSTOM_SOURCE_NOTE, HASH_NOTE, INSTALL_NOTE, SMARTSCREEN_NOTE,
  SOURCE_CHECKOUT_NOTE, appApplyTarget, appDownloadLine, appUpdateLine, canDownload, canDownloadApp, checkLine,
  downloadLine, installTarget, isDownloading, signatureLine, versionLine,
} from './updateModel'

const TITLE = 'App updates'
const SERVER = { pcOnly: true, serverText: true } as const
const POLL_MS = 1000

export function AppUpdatesCard() {
  const pc = usePcOnly()
  const pending = usePcPendingNote(pc)
  if (pending) {
    return (
      <Card title={TITLE} aria-label={TITLE}>
        <p className="muted">{pending}</p>
      </Card>
    )
  }
  return <AppUpdatesControls />
}

function AppUpdatesControls() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [launched, setLaunched] = useState<string | null>(null)
  const [restarting, setRestarting] = useState(false)

  const run = useCallback(async (action: () => Promise<UpdateStatus>) => {
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

  useEffect(() => {
    getUpdateStatus().then(setStatus, setError)
  }, [])

  const downloading = status != null && isDownloading(status)
  useEffect(() => {
    if (!downloading) return
    const timer = window.setInterval(() => {
      getUpdateStatus().then(setStatus, setError)
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [downloading])

  async function install() {
    setBusy(true)
    setError(null)
    try {
      const r = await installUpdate()
      setLaunched(`${r.installer_name}, version ${r.version}`)
    } catch (e) {
      setError(e)
      // A refused install may have dropped the verified download.
      getUpdateStatus().then(setStatus, () => undefined)
    } finally {
      setBusy(false)
    }
  }

  async function applyApp() {
    setBusy(true)
    setError(null)
    try {
      await applyAppUpdate()
      setRestarting(true)
    } catch (e) {
      setError(e)
      getUpdateStatus().then(setStatus, () => undefined)
    } finally {
      setBusy(false)
    }
  }

  const progress = status ? downloadLine(status) : null
  const appLine = status ? appUpdateLine(status) : null
  const appProgress = status ? appDownloadLine(status) : null
  return (
    <Card title={TITLE} meta={status ? versionLine(status) : undefined} aria-label={TITLE}>
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
      {!status ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="settings-form">
          {!status.installed && <p className="muted">{SOURCE_CHECKOUT_NOTE}</p>}
          {status.custom_source && <p className="muted" data-testid="update-custom-source">{CUSTOM_SOURCE_NOTE}</p>}
          <p data-testid="update-check-line">{checkLine(status)}</p>
          <div className="settings-actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy || downloading}
              onClick={() => run(checkForUpdates)}>
              Check for updates
            </button>
            {canDownload(status) && (
              <button type="button" className={buttonClass('primary')} disabled={busy}
                onClick={() => run(downloadUpdate)}>
                Download
              </button>
            )}
            {status.can_install && (
              <ConfirmButton name={installTarget(status)} label="Install and restart…"
                confirmLabel={`Open Setup for ${installTarget(status)}`} verb="install" tone="primary" busy={busy}
                onConfirm={() => void install()} />
            )}
          </div>
          {progress && <p role="status" data-testid="update-download-line">{progress}</p>}
          {status.app_last_result && (
            <p role="status" data-testid="update-app-result">{status.app_last_result}</p>
          )}
          {appLine && <p data-testid="update-app-line">{appLine}</p>}
          {(canDownloadApp(status) || status.can_apply_app) && !restarting && (
            <div className="settings-actions">
              {canDownloadApp(status) && (
                <button type="button" className={buttonClass('primary')} disabled={busy}
                  onClick={() => run(downloadAppUpdate)}>
                  Download app update
                </button>
              )}
              {status.can_apply_app && (
                <ConfirmButton name={appApplyTarget(status)} label="Update app only…"
                  confirmLabel={`Update the app to ${appApplyTarget(status)} and restart`} verb="update to"
                  tone="primary" busy={busy} onConfirm={() => void applyApp()} />
              )}
            </div>
          )}
          {appProgress && <p role="status" data-testid="update-app-download-line">{appProgress}</p>}
          {restarting && <p role="status" data-testid="update-app-restarting">{APP_RESTARTING_LINE}</p>}
          {status.can_apply_app && !restarting && <p className="muted">{APP_APPLY_NOTE}</p>}
          {status.installed && <p className="muted" data-testid="update-signature-line">{signatureLine(status)}</p>}
          {launched && (
            <p role="status">Setup is open ({launched}). {INSTALL_NOTE}</p>
          )}
          {status.update_available && status.notes && (
            <details>
              <summary>What's new in {status.latest}</summary>
              <pre className="result">{status.notes}</pre>
            </details>
          )}
          {status.installed && (
            <>
              <p className="muted">{INSTALL_NOTE}</p>
              <p className="muted">{SMARTSCREEN_NOTE}</p>
              <p className="muted">{HASH_NOTE}</p>
            </>
          )}
          <div className="setting-list">
            <Field label="Check once a day" help={AUTO_CHECK_HELP}>
              <Toggle checked={status.auto_check} disabled={busy}
                onChange={(next) => run(() => setUpdateAutoCheck(next))} />
            </Field>
          </div>
        </div>
      )}
    </Card>
  )
}
