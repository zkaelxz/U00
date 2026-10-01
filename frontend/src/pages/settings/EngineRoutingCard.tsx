/*
 * Settings > "Which engine does what" (roadmap Step 36). Tasks: each kind of
 * AI work picks its engine (saved at once, rolled back on an error). Engines:
 * each engine's status and a Test button that makes one short real call.
 * Loaded on its own, so a failure here leaves the rest of Settings working.
 * Choosing and testing are PC only; away from the PC the controls are
 * disabled with one muted line.
 */
import { useEffect, useState } from 'react'

import { getEngineRouting, setCapabilityEngine, testEngine } from '../../api/engineRouting'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { CapabilityRoute, EngineRouting } from '../../types/engineRouting'
import {
  choiceFromSelect,
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
} from './engineRouting'

const TITLE = 'Which engine does what'

interface Props {
  // Bumped by the page after any key, endpoint or preference save: reload.
  refreshToken: number
  // The "Gemini free tier" setting lives with the engines it changes.
  geminiFreeTier: boolean
  onGeminiFreeTier: (next: boolean) => void
}

export function EngineRoutingCard({ refreshToken, geminiFreeTier, onGeminiFreeTier }: Props) {
  const remote = usePcOnly() === 'remote'
  const [routing, setRouting] = useState<EngineRouting | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [error, setError] = useState<unknown>(null)
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

  return (
    <Card
      title={TITLE}
      meta={routing ? `${workingCount(routing)} of ${routing.engines.length} engines working` : undefined}
      aria-label={TITLE}
      className="routing-card"
    >
      <ErrorBanner error={loadError} />
      {!routing && !loadError && <p className="muted">Loading…</p>}
      {routing && (
        <>
          <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
          {remote && <p className="settings-note">Choosing and testing engines is PC only.</p>}
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
          <h4 className="routing-heading">Engines</h4>
          <div className="setting-list">
            <Field label="Gemini free tier" help="Slows Gemini requests to stay inside the free tier's rate limits.">
              <Toggle checked={geminiFreeTier} onChange={onGeminiFreeTier} />
            </Field>
          </div>
          <p className="settings-note">Test makes one short real call and may cost a fraction of a cent on a paid engine.</p>
          <ul className="status-list" aria-label="Engines">
            {routing.engines.map((e) => {
              const label = humanize('engine', e.engine)
              const badge = statusBadge(e.status)
              const busy = testing.has(e.engine)
              const blocked = testBlockedReason(e)
              const tested = e.last_test ? testedText(e.last_test.tested_at) : ''
              const details = [tagsText(e.tags), blocked ?? tested].filter(Boolean).join(' · ')
              return (
                <li key={e.engine} data-testid={`engine-${e.engine}`}>
                  <div className="status-row">
                    <span className="status-row-name">{label}</span>
                    <Badge tone={badge.tone}>{badge.label}</Badge>
                    <button
                      type="button"
                      className={buttonClass('secondary', 'sm')}
                      aria-label={busy ? `Testing ${label}` : `Test ${label}`}
                      disabled={remote || busy || blocked !== null}
                      title={blocked ?? undefined}
                      onClick={() => void runTest(e.engine)}
                    >
                      {busy ? 'Testing…' : 'Test'}
                    </button>
                  </div>
                  {details && <p className="settings-note">{details}</p>}
                  {e.status === 'failed' && e.last_test?.error && (
                    <p className="error routing-error">{e.last_test.error}</p>
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Card>
  )
}
