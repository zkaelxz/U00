/*
 * Settings > Notifications (Step 44): push a short message to Discord and/or
 * ntfy when a background job finishes or fails, or new chapters are found.
 * "What to send" switches pick which of those events are pushed. PC only. The saved
 * addresses are secrets: the inputs are never pre-filled and the page only
 * ever learns "configured: yes/no". A typed address lives in this
 * component's state until it is sent, then is dropped.
 *
 * Layout (UI refresh §3.12): an always-open Card with one row per channel
 * (name, Set/Missing badge, "Set up"/"Replace" opening its form in place).
 * There is no separate on/off: a channel is on once its address is saved.
 */
import { useEffect, useState } from 'react'

import {
  clearNotificationChannel,
  getNotificationStatus,
  sendTestNotification,
  setNotificationCategories,
  setNotificationChannel,
} from '../../api/notifications'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { NotificationChannel, NotificationStatus } from '../../types/notifications'
import { SAVED_ON_PC_NOTE } from './preferences'
import {
  CATEGORIES,
  CATEGORIES_NOTE,
  CHANNELS,
  type CategoryField,
  categoryChange,
  isConfigured,
  notificationErrorMessage,
  notificationSummary,
  testResultText,
} from './notifications'

const TITLE = 'Notifications'

export function NotificationsSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <NotificationControls />
}

function NotificationControls() {
  const [status, setStatus] = useState<NotificationStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [testing, setTesting] = useState(false)
  const [testNote, setTestNote] = useState<string | null>(null)
  const [open, setOpen] = useState<NotificationChannel | null>(null)
  const [savingCategory, setSavingCategory] = useState(false)

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

  // Saves at once; shows the new value while saving and rolls back on error.
  const setCategory = (field: CategoryField, next: boolean) => {
    if (!status) return
    const previous = status[field]
    setSavingCategory(true)
    setError(null)
    setStatus((cur) => (cur ? { ...cur, [field]: next } : cur))
    setNotificationCategories(categoryChange(field, next)).then(
      (s) => {
        setStatus(s)
        setSavingCategory(false)
      },
      (e: unknown) => {
        setStatus((cur) => (cur ? { ...cur, [field]: previous } : cur))
        setError(e)
        setSavingCategory(false)
      },
    )
  }

  return (
    <Card
      title={TITLE}
      meta={status ? notificationSummary(status) : undefined}
      aria-label={TITLE}
      actions={
        status && (
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={testing || !anyConfigured} onClick={sendTest}>
            {testing ? 'Sending…' : 'Send test'}
          </button>
        )
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      <p className="settings-note">
        A short message when a background job finishes or fails (job type, drama title), or when a
        check of your tracked sources finds new chapters.
      </p>
      {!status ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <ul className="status-list" aria-label="Notification channels">
            {CHANNELS.map((c) => {
              const configured = isConfigured(status, c.channel)
              const expanded = open === c.channel
              return (
                <li key={c.channel}>
                  <div className="status-row">
                    <span className="status-row-name">{c.label}</span>
                    <span data-testid={`notify-${c.channel}`}>
                      <Badge tone={configured ? 'ok' : 'neutral'}>{configured ? 'Set' : 'Missing'}</Badge>
                    </span>
                    <button
                      type="button"
                      className={buttonClass(expanded ? 'ghost' : 'secondary', 'sm')}
                      aria-expanded={expanded}
                      aria-label={expanded ? `Close ${c.label}` : `${configured ? 'Replace' : 'Set up'} ${c.label}`}
                      onClick={() => setOpen(expanded ? null : c.channel)}
                    >
                      {expanded ? 'Close' : configured ? 'Replace' : 'Set up'}
                    </button>
                  </div>
                  {expanded && (
                    <ChannelForm
                      channel={c.channel}
                      configured={configured}
                      onResult={(channel, ok) =>
                        setStatus((cur) =>
                          cur ? { ...cur, [channel === 'discord' ? 'discord_configured' : 'ntfy_configured']: ok } : cur,
                        )
                      }
                    />
                  )}
                </li>
              )
            })}
          </ul>
          {!anyConfigured && <p className="settings-note">Set up Discord or ntfy first to send a test.</p>}
          <p className="muted" role="status" data-testid="notify-test-result">
            {testNote ?? ''}
          </p>
          <div className="settings-group" role="group" aria-labelledby="notify-categories-title">
            <h4 className="settings-subhead" id="notify-categories-title">
              What to send
            </h4>
            <div className="setting-list">
              {CATEGORIES.map((c) => (
                <Field key={c.field} label={c.label} help={c.help}>
                  <Toggle
                    checked={status[c.field]}
                    disabled={savingCategory}
                    onChange={(next) => setCategory(c.field, next)}
                  />
                </Field>
              ))}
            </div>
            <p className="settings-note">{CATEGORIES_NOTE}</p>
          </div>
          <p className="settings-note" data-testid="ntfy-local-note">
            {status.ntfy_allow_local
              ? 'A local ntfy server (on this PC or your home network) is allowed.'
              : 'A local ntfy server needs BAIHE_NTFY_ALLOW_LOCAL=1 in .env on the Baihe PC.'}
          </p>
          <p className="settings-note">{SAVED_ON_PC_NOTE}</p>
        </>
      )}
    </Card>
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
    <div className="status-form">
      <Field label={meta.field} help={meta.help} error={fieldError}>
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          placeholder={configured ? 'Type a new address to replace the saved one' : meta.placeholder}
          disabled={busy}
          autoFocus
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
