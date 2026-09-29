/*
 * Settings > Notifications (Step 44): push a short message to Discord and/or
 * ntfy when a background job finishes or fails. PC only. The saved
 * addresses are secrets: the inputs are never pre-filled and the page only
 * ever learns "configured: yes/no". A typed address lives in this
 * component's state until it is sent, then is dropped.
 */
import { useEffect, useState } from 'react'

import {
  clearNotificationChannel,
  getNotificationStatus,
  sendTestNotification,
  setNotificationChannel,
} from '../../api/notifications'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { NotificationChannel, NotificationStatus } from '../../types/notifications'
import {
  CHANNELS,
  isConfigured,
  notificationErrorMessage,
  notificationSummary,
  testResultText,
} from './notifications'

const STORAGE_KEY = 'settings.notifications'
const TITLE = 'Notifications'
const rowStyle = { display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' } as const

export function NotificationsSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Section title={TITLE} summary={PC_ONLY_SUMMARY} storageKey={STORAGE_KEY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return <NotificationControls />
}

function NotificationControls() {
  const [status, setStatus] = useState<NotificationStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [testing, setTesting] = useState(false)
  const [testNote, setTestNote] = useState<string | null>(null)

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getNotificationStatus().then(
        (s) => live && setStatus(s),
        (e: unknown) => live && setError(e),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const anyConfigured = !!status && CHANNELS.some((c) => isConfigured(status, c.channel))

  const sendTest = () => {
    setTesting(true)
    setTestNote(null)
    sendTestNotification().then(
      (r) => {
        setTestNote(testResultText(r) || 'Nothing is set up yet.')
        setTesting(false)
      },
      (e: unknown) => {
        setTestNote(notificationErrorMessage(e))
        setTesting(false)
      },
    )
  }

  return (
    <Section
      title={TITLE}
      storageKey={STORAGE_KEY}
      summary={status ? notificationSummary(status) : undefined}
    >
      <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
        <p className="muted">
          Sends a short message (job type, drama title, finished or failed) when a background job
          ends. Addresses are saved to .env on the Baihe PC and never shown again. Setting them works
          only on that PC with BAIHE_API_ALLOW_KEY_WRITES=1.
        </p>
        {!status ? (
          !error && <p className="muted">Loading…</p>
        ) : (
          <>
            {CHANNELS.map((c) => (
              <ChannelForm
                key={c.channel}
                channel={c.channel}
                configured={isConfigured(status, c.channel)}
                onResult={(channel, configured) =>
                  setStatus((cur) =>
                    cur
                      ? { ...cur, [channel === 'discord' ? 'discord_configured' : 'ntfy_configured']: configured }
                      : cur,
                  )
                }
              />
            ))}
            <p className="muted" data-testid="ntfy-local-note">
              {status.ntfy_allow_local
                ? 'A local ntfy server (on this PC or your home network) is allowed.'
                : 'A local ntfy server needs BAIHE_NTFY_ALLOW_LOCAL=1 in .env on the Baihe PC.'}
            </p>
            <div style={rowStyle}>
              <button type="button" disabled={testing || !anyConfigured} onClick={sendTest}>
                {testing ? 'Sending…' : 'Send test'}
              </button>
              {!anyConfigured && <span className="muted">Set up Discord or ntfy first.</span>}
            </div>
            <p className="muted" role="status" data-testid="notify-test-result">
              {testNote ?? ''}
            </p>
          </>
        )}
      </div>
    </Section>
  )
}

type FormProps = {
  channel: NotificationChannel
  configured: boolean
  onResult: (channel: NotificationChannel, configured: boolean) => void
}

function ChannelForm({ channel, configured, onResult }: FormProps) {
  const meta = CHANNELS.find((c) => c.channel === channel)!
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)

  const run = (call: () => Promise<{ configured: boolean }>, done: (configured: boolean) => string) => {
    setBusy(true)
    setNotice(null)
    setFieldError(null)
    setDraft('') // the request already holds the value; drop it now
    call().then(
      (r) => {
        onResult(channel, r.configured)
        setNotice(done(r.configured))
        setBusy(false)
      },
      (e: unknown) => {
        setFieldError(notificationErrorMessage(e))
        setBusy(false)
      },
    )
  }

  const save = () => {
    const value = draft.trim()
    if (!value) return
    run(
      () => setNotificationChannel(channel, value),
      (ok) => (ok ? 'Saved.' : 'Saved, but it is not being picked up.'),
    )
  }

  const clear = () =>
    run(
      () => clearNotificationChannel(channel),
      (ok) => (ok ? 'Removed from .env, but a system environment variable still sets it.' : 'Cleared.'),
    )

  return (
    <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
      <Field label={meta.field} help={meta.help} error={fieldError}>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          placeholder={configured ? 'Configured (type a new address to replace it)' : meta.placeholder}
          disabled={busy}
          onChange={(e) => {
            setDraft(e.target.value)
            setNotice(null)
            setFieldError(null)
          }}
        />
      </Field>
      <div style={rowStyle}>
        <span className="muted" data-testid={`notify-${channel}`}>
          {configured ? 'Configured: yes' : 'Configured: no'}
        </span>
        <ConfirmButton
          label="Save…"
          ariaLabel={`Save ${meta.label} address`}
          verb="save"
          tone="primary"
          name={`${meta.label} address`}
          busy={busy}
          disabled={!draft.trim()}
          onConfirm={save}
        />
        <ConfirmButton
          label="Clear…"
          ariaLabel={`Clear ${meta.label} address`}
          verb="clear"
          name={`${meta.label} address`}
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
    </div>
  )
}
