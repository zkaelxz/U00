/*
 * Settings > Browser extension: turn the extension bridge on or off, pick
 * the engine it translates pages with, and show its token (PC only). The
 * engine's key stays on the PC; only whether one is saved comes back. The token lives only in this component's state:
 * never persisted, never logged, and cleared on Hide, on unmount and after
 * 120 s.
 *
 * Layout (UI refresh §3.12): a Card with the on/off Toggle in its header;
 * the engine picker and token show only while the bridge is on.
 */
import { useEffect, useRef, useState } from 'react'

import {
  getExtensionEngine, getExtensionStatus, revealExtensionToken, setExtensionEnabled, setExtensionEngine,
} from '../../api/extension'
import { Card } from '../../components/Card'
import { copyText } from '../../components/clipboard'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly, usePcPendingNote } from '../../hooks/usePcOnly'
import type { ExtensionEngineSettings, ExtensionStatus } from '../../types/extension'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { humanize } from '../../components/labels'
import {
  COPIED_MS, TOKEN_VISIBLE_MS, copyFallbackText, extensionEngineNote, extensionSummary, extensionToggleNote,
} from '../diagnostics/diagnosticsAdmin'
import '../diagnostics/diagnostics.css'

const SERVER = { pcOnly: true, serverText: true } as const
const TITLE = 'Browser extension'
const HELP = 'Lets the browser extension on this PC send pages to Baihe to translate.'

export function ExtensionSection() {
  const pc = usePcOnly()
  const pending = usePcPendingNote(pc)
  if (pending) {
    return (
      <Card title={TITLE} aria-label={TITLE}>
        <p className="muted" data-testid="pc-pending">{pending}</p>
      </Card>
    )
  }
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <ExtensionControls />
}

function ExtensionControls() {
  const [status, setStatus] = useState<ExtensionStatus | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)

  // Mounted only once /api/meta said this is the main PC.
  useEffect(() => {
    let live = true
    getExtensionStatus().then(
      (s) => live && setStatus(s),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [])

  const toggle = (enabled: boolean) => {
    if (!status) return
    const previous = status
    setSaving(true)
    setError(null)
    setNote(null)
    setStatus({ ...status, enabled }) // optimistic; rolled back on error
    setExtensionEnabled(enabled).then(
      (r) => {
        setStatus({ enabled: r.enabled, running: r.running })
        setNote(extensionToggleNote(r))
        setSaving(false)
      },
      (e: unknown) => {
        setStatus(previous)
        setError(e)
        setSaving(false)
      },
    )
  }

  return (
    <Card
      title={TITLE}
      aria-label={TITLE}
      meta={
        status && (
          <span aria-live="polite" data-testid="extension-note">
            {note ?? extensionSummary(status)}
          </span>
        )
      }
      actions={
        status && (
          <Toggle aria-label="Extension bridge" checked={status.enabled} disabled={saving} onChange={toggle} />
        )
      }
    >
      <div className="diag-stack">
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={SERVER} />
        <p className="settings-note">{HELP}</p>
        {!status
          ? !error && <p className="muted">Loading…</p>
          : status.enabled && (
              <>
                <EnginePicker />
                <TokenReveal />
              </>
            )}
      </div>
    </Card>
  )
}

function EnginePicker() {
  const [settings, setSettings] = useState<ExtensionEngineSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getExtensionEngine().then(
      (s) => live && setSettings(s),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [])

  const save = (engine: string | null, model: string | null) => {
    setSaving(true)
    setError(null)
    setExtensionEngine(engine, model).then(
      (s) => {
        setSettings(s)
        setSaving(false)
      },
      (e: unknown) => {
        setError(e)
        setSaving(false)
      },
    )
  }

  const models = settings?.engines.find((e) => e.name === settings.engine)?.models ?? null

  return (
    <div className="diag-stack">
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={SERVER} />
      {settings && (
        <>
          <div className="field-row">
            <Field label="Translate pages with" help="Uses the key saved on this PC for that engine.">
              <select
                value={settings.engine ?? ''}
                disabled={saving}
                onChange={(e) => save(e.target.value || null, null)}
              >
                <option value="">None (original text only)</option>
                {settings.engines.map((e) => (
                  <option key={e.name} value={e.name}>
                    {e.label || humanize('engine', e.name)}
                    {e.key_configured ? '' : ' (no key)'}
                  </option>
                ))}
              </select>
            </Field>
            {models && (
              <Field label="Model">
                <select
                  value={settings.model ?? ''}
                  disabled={saving}
                  onChange={(e) => save(settings.engine, e.target.value || null)}
                >
                  <option value="">Default</option>
                  {models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
          <p className="muted" aria-live="polite" data-testid="extension-engine-note">
            {extensionEngineNote(settings)}
          </p>
        </>
      )}
    </div>
  )
}

function TokenReveal() {
  const [token, setToken] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [announce, setAnnounce] = useState('')
  const [error, setError] = useState<unknown>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const touch = useMediaQuery('(pointer: coarse)')

  useEffect(() => {
    if (announce !== 'Copied.') return
    const t = setTimeout(() => setAnnounce(''), COPIED_MS)
    return () => clearTimeout(t)
  }, [announce])

  // Auto-hide after 120 s; unmounting drops the state with it.
  useEffect(() => {
    if (token === null) return
    const t = setTimeout(() => {
      setToken(null)
      setAnnounce('Token hidden.')
    }, TOKEN_VISIBLE_MS)
    return () => clearTimeout(t)
  }, [token])

  const reveal = () => {
    setBusy(true)
    setError(null)
    setAnnounce('')
    revealExtensionToken().then(
      (r) => {
        setToken(r.token)
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const copy = async () => {
    if (await copyText(token ?? '')) {
      setAnnounce('Copied.')
    } else {
      inputRef.current?.focus() // selects it (onFocus)
      setAnnounce(copyFallbackText(touch))
    }
  }

  return (
    <div className="diag-stack">
      {token === null ? (
        <div className="actions">
          <ConfirmButton
            label="Show token…"
            ariaLabel="Show extension token"
            verb="show"
            tone="primary"
            name="extension token"
            busy={busy}
            onConfirm={reveal}
          />
        </div>
      ) : (
        <div className="token-row">
          <input
            ref={inputRef}
            readOnly
            value={token}
            autoComplete="off"
            spellCheck={false}
            aria-label="Extension token"
            onFocus={(e) => e.currentTarget.select()}
          />
          <button type="button" className={buttonClass('secondary')} onClick={() => void copy()}>
            Copy
          </button>
          <button
            type="button"
            className={buttonClass('ghost')}
            onClick={() => {
              setToken(null)
              setAnnounce('Token hidden.')
            }}
          >
            Hide
          </button>
        </div>
      )}
      <span className="muted" aria-live="polite" data-testid="token-announce">
        {announce}
      </span>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={SERVER} />
    </div>
  )
}
