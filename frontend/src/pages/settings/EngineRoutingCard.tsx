/*
 * Settings > "Which engine does what". Tasks: each kind of AI work picks its
 * engine (saved at once, rolled back on an error). Engines and keys: ONE list,
 * a row per engine with free or paid, key status, status, Test and the key
 * form. Keys are write-only: the page only learns whether one is set. The
 * routing loads on its own, so a failure leaves the key rows working.
 * Choosing, testing and setting keys are PC only; away from the PC those
 * controls are disabled or hidden with one muted line.
 */
import { useEffect, useState } from 'react'

import { getEngineRouting, setCapabilityEngine, testEngine } from '../../api/engineRouting'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { SettingsKeyForm } from '../SettingsKeyForm'
import { Toggle } from '../../components/Toggle'
import { humanize } from '../../components/labels'
import { summarizeEngineFailure } from '../../components/errorMessages'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { CapabilityRoute, EngineRouting } from '../../types/engineRouting'
import type { EngineKeyResult, SettingsOverview } from '../../types/settings'
import { SAVED_ON_PC_NOTE } from './preferences'
import {
  choiceFromSelect,
  engineRows,
  keysSetCount,
  replaceCapability,
  replaceEngine,
  selectValue,
  statusBadge,
  tagsText,
  testBlockedReason,
  testedText,
  unsetBadge,
  unsetOptionLabel,
  withChoice,
  workingCount,
  type KeyState,
} from './engineRouting'

const TITLE = 'Which engine does what'
const KEY_BADGE: Record<KeyState, string> = { set: 'Set', missing: 'Missing', none: 'Not needed' }

interface Props {
  // Bumped by the page after any key, endpoint or preference save: reload.
  refreshToken: number
  // The "Gemini free tier" setting lives with the engines it changes.
  settings: SettingsOverview
  onKey: (r: EngineKeyResult) => void
  geminiFreeTier: boolean
  onGeminiFreeTier: (next: boolean) => void
}

export function EngineRoutingCard({ refreshToken, settings, onKey, geminiFreeTier, onGeminiFreeTier }: Props) {
  const remote = usePcOnly() === 'remote'
  const [routing, setRouting] = useState<EngineRouting | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [error, setError] = useState<unknown>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [testing, setTesting] = useState<ReadonlySet<string>>(new Set())

  useEffect(() => {
    let live = true
    getEngineRouting().then(
      (r) => {
        if (!live) return
        setRouting(r)
        setLoadError(null)
      },
      (e: unknown) => live && setLoadError(e),
    )
    return () => {
      live = false
    }
  }, [refreshToken])

  async function choose(cap: CapabilityRoute, engine: string | null) {
    setError(null)
    setRouting((cur) => (cur ? replaceCapability(cur, withChoice(cap, engine)) : cur)) // optimistic
    try {
      const saved = await setCapabilityEngine(cap.id, engine)
      setRouting((cur) => (cur ? replaceCapability(cur, saved) : cur))
    } catch (e) {
      setRouting((cur) => (cur ? replaceCapability(cur, cap) : cur)) // roll back
      setError(e)
    }
  }

  async function runTest(engine: string) {
    setError(null)
    setTesting((cur) => new Set(cur).add(engine))
    try {
      const status = await testEngine(engine)
      setRouting((cur) => (cur ? replaceEngine(cur, status) : cur))
    } catch (e) {
      setError(e)
    } finally {
      setTesting((cur) => {
        const next = new Set(cur)
        next.delete(engine)
        return next
      })
    }
  }

  const rows = engineRows(routing, settings.engine_keys)
  const keys = keysSetCount(rows)
  const meta = [`${keys.set} of ${keys.total} keys set`, routing ? `${workingCount(routing)} working` : ''].filter(Boolean).join(' · ')

  return (
    <Card
      title={TITLE}
      meta={meta}
      aria-label={TITLE}
      className="routing-card"
    >
      <div className="setting-list">
        <Field label="Gemini free tier" help="Slows Gemini requests to stay inside the free tier's rate limits.">
          <Toggle checked={geminiFreeTier} onChange={onGeminiFreeTier} />
        </Field>
      </div>
      <ErrorBanner error={loadError} />
      {!routing && !loadError && <p className="muted">Loading…</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      {remote && <p className="settings-note">Choosing engines, testing and setting keys is PC only.</p>}
      {routing && (
        <>
          <h4 className="routing-heading">Tasks</h4>
          <ul className="status-list" aria-label="Tasks">
            {routing.capabilities.map((cap) => (
              <li key={cap.id} data-testid={`task-${cap.id}`}>
                <Field label={cap.label} help={cap.help}>
                  <select
                    value={selectValue(cap)}
                    disabled={remote}
                    onChange={(e) => void choose(cap, choiceFromSelect(e.target.value))}
                  >
                    <option value="">{unsetOptionLabel(cap, (e) => humanize('engine', e))}</option>
                    {cap.choices.map((name) => (
                      <option key={name} value={name}>
                        {humanize('engine', name)}
                      </option>
                    ))}
                  </select>
                </Field>
                {(cap.is_default || !cap.engine_supported) && (
                  <div className="routing-task-meta">
                    {cap.is_default && <Badge>{unsetBadge(cap)}</Badge>}
                    {!cap.engine_supported && (
                      <span className="warn">
                        {humanize('engine', cap.engine)} can't do this task. Pick another engine.
                      </span>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <h4 className="routing-heading">Engines and keys</h4>
      <p className="settings-note">
        {SAVED_ON_PC_NOTE} Test makes one short real call and may cost a fraction of a cent on a paid engine.
      </p>
      <ul className="status-list engine-list" aria-label="Engines">
        {rows.map((row) => {
          const { engine, label, status: e, keyState } = row
          const expanded = openKey === engine
          const busy = testing.has(engine)
          const blocked = e ? testBlockedReason(e) : null
          const tested = e?.last_test ? testedText(e.last_test.tested_at) : ''
          const badge = e && e.status !== 'not_configured' ? statusBadge(e.status) : null
          const details = [
            row.cost,
            keyState === 'set' ? 'Key saved on the Baihe PC' : keyState === 'none' ? 'No key needed' : '',
            e ? tagsText(e.tags.filter((t) => t !== 'local')) : '',
            (e && blocked && e.status !== 'not_configured' ? blocked : '') || tested,
          ]
            .filter(Boolean)
            .join(' · ')
          return (
            <li key={engine} data-testid={`engine-${engine}`}>
              <div className="status-row engine-row">
                <span className="status-row-name">{label}</span>
                <span data-testid={`key-${engine}`}>
                  <Badge tone={keyState === 'set' ? 'ok' : 'neutral'}>{KEY_BADGE[keyState]}</Badge>
                </span>
                {badge && <Badge tone={badge.tone}>{badge.label}</Badge>}
                {e && (
                  <button
                    type="button"
                    className={buttonClass('secondary', 'sm')}
                    aria-label={busy ? `Testing ${label}` : `Test ${label}`}
                    disabled={remote || busy || blocked !== null}
                    title={blocked ?? undefined}
                    onClick={() => void runTest(engine)}
                  >
                    {busy ? 'Testing…' : 'Test'}
                  </button>
                )}
                {row.writable && !remote && (
                  <button
                    type="button"
                    className={buttonClass(expanded ? 'ghost' : 'secondary', 'sm')}
                    aria-expanded={expanded}
                    aria-label={expanded ? `Close ${label} key` : keyState === 'set' ? `Replace ${label} key` : `Set key for ${label}`}
                    onClick={() => setOpenKey(expanded ? null : engine)}
                  >
                    {expanded ? 'Close' : keyState === 'set' ? 'Replace' : 'Set key'}
                  </button>
                )}
              </div>
              {details && <p className="settings-note">{details}</p>}
              {e && e.status === 'failed' && e.last_test?.error && (
                <div className="routing-error-block">
                  <p className="error routing-error">{summarizeEngineFailure(engine, e.last_test.error, label).summary}</p>
                  <details className="routing-error-details">
                    <summary>Details</summary>
                    <p className="settings-note">{e.last_test.error}</p>
                  </details>
                </div>
              )}
              {expanded && <SettingsKeyForm engine={engine} label={label} configured={keyState === 'set'} onResult={onKey} />}
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
