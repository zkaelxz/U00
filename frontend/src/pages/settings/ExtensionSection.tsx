/*
 * Settings > Browser extension: turn the extension bridge on or off, pick
 * the engine it translates pages with, and show its token (PC only). The
 * engine's key stays on the PC; only whether one is saved comes back. The token lives only in this component's state:
 * never persisted, never logged, and cleared on Hide, on unmount and after
 * 120 s.
 */
import { useEffect, useRef, useState } from 'react'

import {
  getExtensionEngine, getExtensionStatus, revealExtensionToken, setExtensionEnabled, setExtensionEngine,
} from '../../api/extension'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly, usePcPendingNote } from '../../hooks/usePcOnly'
import type { ExtensionEngineSettings, ExtensionStatus } from '../../types/extension'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import {
  COPIED_MS, TOKEN_VISIBLE_MS, copyFallbackText, extensionEngineNote, extensionSummary, extensionToggleNote,
} from '../diagnostics/diagnosticsAdmin'
import '../diagnostics/diagnostics.css'

const SERVER = { pcOnly: true, serverText: true } as const

export function ExtensionSection() {
  const pc = usePcOnly()
  const pending = usePcPendingNote(pc)
  if (pending) {
    return (
      <Section title="Browser extension" storageKey="settings.extension">
        <p className="muted" data-testid="pc-pending">{pending}</p>
      </Section>
    )
  }
  if (pc === 'remote') {
    return (
      <Section title="Browser extension" summary={PC_ONLY_SUMMARY} storageKey="settings.extension">
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
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
    <Section
      title="Browser extension"
      storageKey="settings.extension"
      summary={status ? extensionSummary(status) : undefined}
    >
      <div className="diag-stack">
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={SERVER} />
        {!status ? (
          !error && <p className="muted">Loading…</p>
        ) : (
          <>
            <div className="toggle-list">
              <Field label="Extension bridge" help="Lets the browser extension on this PC send pages to Baihe.">
                <input
                  type="checkbox"
                  checked={status.enabled}
                  disabled={saving}
                  onChange={(e) => toggle(e.target.checked)}
                />
              </Field>
            </div>
            {/* The section summary hides while open, so the status stays here. */}
            <p className="muted" aria-live="polite" data-testid="extension-note">
              {note ?? extensionSummary(status)}
            </p>
            <EnginePicker />
            <TokenReveal />
          </>
        )}
      </div>
    </Section>
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
                    {e.name}
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
    try {
      if (!navigator.clipboard?.writeText) throw new Error('no clipboard')
      await navigator.clipboard.writeText(token ?? '')
      setAnnounce('Copied.')
    } catch {
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
          <button type="button" onClick={() => void copy()}>
            Copy
          </button>
          <button
            type="button"
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
