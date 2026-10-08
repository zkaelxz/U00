/*
 * Settings > Browser extension devices: per-computer tokens the browser
 * extension will use to reach Baihe from away from the main PC. A signed-in
 * person adds a device by name and gets its token once (copy it into the
 * extension's options; Baihe keeps only a fingerprint), sees when each was
 * last used, and revokes one after a confirm step. The owner (or an admin)
 * at the main PC also sees everyone's devices and can revoke any. The token
 * lives only in this component's state until Done or unmount: never
 * persisted, never logged. The PC-only shared token for the extension on
 * the main PC itself stays in the Browser extension card.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  createDeviceToken, listAllDeviceTokens, listMyDeviceTokens, revokeAnyDeviceToken, revokeMyDeviceToken,
} from '../../api/extensionDevices'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { copyText } from '../../components/clipboard'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { usePcOnly } from '../../hooks/usePcOnly'
import { useSession } from '../../hooks/useSession'
import type { AdminDeviceToken, DeviceToken, DeviceTokenList } from '../../types/extensionDevices'
import { copyFallbackText } from '../diagnostics/diagnosticsAdmin'
import '../diagnostics/diagnostics.css'
import {
  DEFAULT_EXPIRY_DAYS, EXPIRY_CHOICES, MAX_DEVICE_LABEL_CHARS, NO_SEND_PERMISSION, SHOWN_ONCE_WARNING,
  activeCount, adminTokensBlock, extensionDevicesView, labelProblem, tokenDetails,
} from './extensionDevicesModel'

const TITLE = 'Browser extension devices'

export function ExtensionDevicesCard() {
  const session = useSession()
  const pc = usePcOnly()
  const view = extensionDevicesView(session, pc)
  if (!view.own && !view.all) return null
  return (
    <Card title={TITLE} aria-label={TITLE} className="extension-devices-card">
      <div className="settings-group" data-testid="extension-devices">
        <p className="settings-note">
          Each computer's browser extension gets its own token. Revoke one to cut that computer off at once.
        </p>
        {view.own && <OwnDevices canCreate={view.canCreate} block={adminTokensBlock(session, pc)} />}
        {view.all && <AllDevices />}
      </div>
    </Card>
  )
}

function OwnDevices({ canCreate, block }: { canCreate: boolean; block: string | null }) {
  const [data, setData] = useState<DeviceTokenList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | 'create' | null>(null)
  const [note, setNote] = useState('')
  const [created, setCreated] = useState<{ token: string; label: string } | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  // Leaves the error alone: the reload after a refused action must not hide why.
  const load = useCallback(() => {
    listMyDeviceTokens().then((r) => {
      setNowMs(Date.now())
      setData(r)
    }, setError)
  }, [])
  useEffect(load, [load])

  const create = async (label: string, days: number | null) => {
    setBusy('create')
    setNote('')
    setError(null)
    try {
      const r = await createDeviceToken(label, days)
      setCreated({ token: r.token, label: r.device_token.label })
      return true
    } catch (e) {
      setError(e)
      return false
    } finally {
      setBusy(null)
      load()
    }
  }

  const revoke = async (t: DeviceToken) => {
    setBusy(t.id)
    setNote('')
    setError(null)
    try {
      await revokeMyDeviceToken(t.id)
      setNote(`Revoked ${t.label}.`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      load()
    }
  }

  const list = data?.tokens ?? []
  const full = data !== null && activeCount(list) >= data.max_active
  return (
    <div className="diag-stack">
      <h4 className="settings-subhead">Your devices</h4>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {created && <ShownOnce token={created.token} label={created.label} onDone={() => setCreated(null)} />}
      {data === null ? (
        !error && <p className="muted">Loading…</p>
      ) : list.length === 0 ? (
        <p className="muted">No devices yet.</p>
      ) : (
        <TokenList tokens={list} nowMs={nowMs} busy={busy} block={block} onRevoke={revoke} />
      )}
      {!canCreate ? (
        <p className="settings-note">{NO_SEND_PERMISSION}</p>
      ) : block ? (
        <p className="settings-note" id="ext-devices-why">{block}</p>
      ) : full ? (
        <p className="settings-note">{`You have ${data?.max_active} devices. Revoke one to add another.`}</p>
      ) : (
        created === null && <AddDevice busy={busy === 'create'} disabled={busy !== null} onCreate={create} />
      )}
      <span className="muted" aria-live="polite">{note}</span>
    </div>
  )
}

function AddDevice({ busy, disabled, onCreate }: {
  busy: boolean
  disabled: boolean
  onCreate: (label: string, days: number | null) => Promise<boolean>
}) {
  const [label, setLabel] = useState('')
  const [days, setDays] = useState<number | null>(DEFAULT_EXPIRY_DAYS)
  const [touched, setTouched] = useState(false)
  const problem = labelProblem(label)
  const submit = async () => {
    setTouched(true)
    if (problem) return
    if (await onCreate(label.trim(), days)) {
      setLabel('')
      setTouched(false)
    }
  }
  return (
    <form className="diag-stack" onSubmit={(e) => { e.preventDefault(); void submit() }}>
      <Field label="Device name" error={touched ? problem : null}>
        <input value={label} maxLength={MAX_DEVICE_LABEL_CHARS} autoComplete="off" placeholder="Work laptop"
          onChange={(e) => setLabel(e.target.value)} />
      </Field>
      <Field label="Expires">
        <select value={days === null ? 'never' : String(days)}
          onChange={(e) => setDays(e.target.value === 'never' ? null : Number(e.target.value))}>
          {EXPIRY_CHOICES.map((c) => (
            <option key={c.label} value={c.days === null ? 'never' : String(c.days)}>{c.label}</option>
          ))}
        </select>
      </Field>
      <div className="settings-actions">
        <button type="submit" className={buttonClass('primary')} disabled={disabled} aria-busy={busy}>
          {busy ? 'Working…' : 'Add device'}
        </button>
      </div>
    </form>
  )
}

function ShownOnce({ token, label, onDone }: { token: string; label: string; onDone: () => void }) {
  const touch = useMediaQuery('(pointer: coarse)')
  const inputRef = useRef<HTMLInputElement>(null)
  const [announce, setAnnounce] = useState('')
  const copy = async () => {
    if (await copyText(token)) {
      setAnnounce('Copied.')
    } else {
      inputRef.current?.focus() // selects it (onFocus)
      setAnnounce(copyFallbackText(touch))
    }
  }
  return (
    <div className="diag-stack" role="group" aria-label={`New token for ${label}`} data-testid="new-device-token">
      <p className="settings-note"><strong>{`Token for ${label}.`}</strong> {SHOWN_ONCE_WARNING}</p>
      <div className="token-row">
        <input ref={inputRef} readOnly value={token} autoComplete="off" spellCheck={false}
          aria-label={`Token for ${label}`} onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className={buttonClass('secondary')} onClick={() => void copy()}>Copy</button>
        <button type="button" className={buttonClass('ghost')} onClick={onDone}>Done</button>
      </div>
      <span className="muted" aria-live="polite">{announce}</span>
    </div>
  )
}

function TokenList<T extends DeviceToken>({ tokens, nowMs, busy, block, onRevoke, owner }: {
  tokens: T[]
  nowMs: number
  busy: number | 'create' | null
  block: string | null
  onRevoke: (t: T) => void
  owner?: (t: T) => string
}) {
  return (
    <ul className="status-list">
      {tokens.map((t) => {
        const name = owner ? `${t.label} (${owner(t)})` : t.label
        return (
          <li key={t.id}>
            <div className="status-row">
              <span className="status-row-name">{name}</span>
              {t.status !== 'active' && <Badge tone="neutral">{t.status === 'revoked' ? 'Revoked' : 'Expired'}</Badge>}
            </div>
            <p className="settings-note">{tokenDetails(t, nowMs)}</p>
            {t.status === 'active' && (
              <div className="settings-actions">
                <ConfirmButton name={name} label="Revoke…" verb="revoke" busy={busy === t.id}
                  disabled={busy !== null || !!block} describedBy={block ? 'ext-devices-why' : undefined}
                  onConfirm={() => onRevoke(t)} />
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function AllDevices() {
  const [list, setList] = useState<AdminDeviceToken[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [nowMs, setNowMs] = useState(() => Date.now())

  const load = useCallback(() => {
    listAllDeviceTokens().then((r) => {
      setNowMs(Date.now())
      setList(r.tokens)
    }, setError)
  }, [])
  useEffect(load, [load])

  const revoke = async (t: AdminDeviceToken) => {
    setBusy(t.id)
    setNote('')
    setError(null)
    try {
      await revokeAnyDeviceToken(t.id)
      setNote(`Revoked ${t.label} (${t.user_name}).`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      load()
    }
  }

  return (
    <div className="diag-stack" data-testid="all-extension-devices">
      <h4 className="settings-subhead">Everyone's devices</h4>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {list === null ? (
        !error && <p className="muted">Loading…</p>
      ) : list.length === 0 ? (
        <p className="muted">No devices yet. Household members add their own here once they sign in.</p>
      ) : (
        <TokenList tokens={list} nowMs={nowMs} busy={busy} block={null} onRevoke={(t) => void revoke(t)}
          owner={(t) => t.user_name} />
      )}
      <span className="muted" aria-live="polite">{note}</span>
    </div>
  )
}
