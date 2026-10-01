/*
 * Settings > Remote access: the public-address check the remote-access
 * health monitor uses to see whether the public name still points at this
 * PC (services/remote_health_service.py). PC only. The saved address may
 * carry a token, so it is write-only: the input is never pre-filled, the page
 * only learns "configured: yes/no", and Test shows a state and a fixed
 * message, never an address. The monitor picks a change up on its next check.
 */
import { useEffect, useState } from 'react'

import { clearIpCheck, getIpCheckStatus, setIpCheck, testIpCheck } from '../../api/diagnostics'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { RemoteIpCheckTestResult } from '../../types/diagnostics'
import { SAVED_ON_PC_NOTE } from './preferences'
import { IP_CHECK_PLACEHOLDER, draftProblem, ipCheckErrorMessage, testBadge } from './remoteIpCheck'

const TITLE = 'Remote access'

export function RemoteAccessSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <IpCheckControls />
}

function IpCheckControls() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<RemoteIpCheckTestResult | null>(null)
  const [testError, setTestError] = useState<string | null>(null)

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getIpCheckStatus().then(
        (s) => live && setConfigured(s.configured),
        (e: unknown) => live && setLoadError(e),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const run = (call: () => Promise<{ configured: boolean }>, done: (ok: boolean) => string) => {
    setBusy(true)
    setNotice(null)
    setFieldError(null)
    setResult(null)
    setDraft('') // the request already holds the value; drop it now
    call().then(
      (r) => {
        setConfigured(r.configured)
        setNotice(done(r.configured))
        setBusy(false)
      },
      (e: unknown) => {
        setFieldError(ipCheckErrorMessage(e))
        setBusy(false)
      },
    )
  }

  const save = () => {
    const value = draft.trim()
    if (!value || draftProblem(value)) return
    run(() => setIpCheck(value), (ok) => (ok ? 'Saved. The next check uses it.' : 'Saved, but it is not being picked up.'))
  }

  const clear = () =>
    run(clearIpCheck, (ok) => (ok ? 'Removed from .env, but a system environment variable still sets it.' : 'Cleared.'))

  const test = () => {
    setTesting(true)
    setResult(null)
    setTestError(null)
    testIpCheck().then(
      (r) => {
        setResult(r)
        setTesting(false)
      },
      (e: unknown) => {
        setTestError(ipCheckErrorMessage(e, false))
        setTesting(false)
      },
    )
  }

  const problem = draftProblem(draft)
  const badge = result ? testBadge(result) : null

  return (
    <Card
      title={TITLE}
      meta={configured == null ? undefined : configured ? 'Address check set' : 'Address check not set'}
      aria-label={TITLE}
      actions={
        configured && (
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={testing || busy} onClick={test}>
            {testing ? 'Testing…' : 'Test'}
          </button>
        )
      }
    >
      <ErrorBanner error={loadError} onDismiss={() => setLoadError(null)} describe={{ pcOnly: true }} />
      <p className="settings-note">
        Optional. An https address that answers with this PC&apos;s public IP address, used to check that the name
        other devices open still points here (dynamic DNS). Without it, that check is skipped.
      </p>
      {configured == null ? (
        !loadError && <p className="muted">Loading…</p>
      ) : (
        <div className="status-form">
          <Field label="Public address check" error={fieldError ?? problem}>
            <input
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={draft}
              placeholder={configured ? 'Type a new address to replace the saved one' : IP_CHECK_PLACEHOLDER}
              disabled={busy}
              onChange={(e) => {
                setDraft(e.target.value)
                setNotice(null)
                setFieldError(null)
              }}
            />
          </Field>
          <div className="settings-actions">
            <ConfirmButton
              label="Save…"
              ariaLabel="Save public address check"
              verb="save"
              tone="primary"
              name="public address check"
              busy={busy}
              disabled={!draft.trim() || !!problem}
              onConfirm={save}
            />
            <ConfirmButton
              label="Clear…"
              ariaLabel="Clear public address check"
              verb="clear"
              name="public address check"
              busy={busy}
              disabled={!configured}
              onConfirm={clear}
            />
          </div>
          {notice && (
            <p className="muted" role="status">
              {notice}
            </p>
          )}
          <p className="muted" role="status" data-testid="ip-check-test-result">
            {badge && result && (
              <>
                <Badge tone={badge.tone}>{badge.label}</Badge> {result.message}
              </>
            )}
            {testError}
          </p>
          <p className="settings-note">{SAVED_ON_PC_NOTE}</p>
        </div>
      )}
    </Card>
  )
}
