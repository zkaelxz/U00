import { useEffect, useState } from 'react'

import { getStyle, learnStyle, resetStyle, setStyleApplied } from '../../../../api/reviewExtras'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import type { StyleState } from '../../../../types/reviewExtras'
import { styleSummary } from './aiExtrasLogic'

interface Props {
  dramaId: number
  reloads: number
}

// Learn my style: one LLM call over every recorded edit; the learned
// preferences go into future translations (series-wide, else global) unless
// paused here.
export function AiExtrasStyle({ dramaId, reloads }: Props) {
  const [state, setState] = useState<StyleState | null>(null)
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getStyle(dramaId).then(
      (s) => !cancelled && setState(s),
      (e) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const run = (p: Promise<StyleState>) => {
    setBusy(true)
    setError(null)
    p.then(setState, setError).finally(() => setBusy(false))
  }

  const profile = state?.profile ?? null
  const tooFew = !!state && state.edit_count < state.min_samples
  const scope = state?.scope === 'series' ? 'this series' : 'all projects'

  return (
    <Section storageKey="review.aiExtras.style" title="Learn my style" summary={state ? styleSummary(state) : 'Loading…'}>
      <p className="muted">
        Finds consistent patterns in the lines you rewrote and adds them to future translation prompts for {scope}.
      </p>
      {state && <p data-testid="style-summary">{styleSummary(state)}</p>}
      {state?.message && (
        <p role="status" className="muted">
          {state.message}
        </p>
      )}
      {profile && (
        <div className="stack">
          {profile.summary && <p className="muted">{profile.summary}</p>}
          <ul data-testid="style-preferences">
            {profile.preferences.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
          <label className="review-check">
            <input
              type="checkbox"
              checked={profile.applied}
              disabled={busy}
              onChange={(e) => run(setStyleApplied(dramaId, e.target.checked))}
            />{' '}
            Use in future translations
          </label>
        </div>
      )}
      <Section title="Advanced" summary={`engine ${engine || 'default'} · model ${model || 'default'}`}>
        <div className="review-edit-row">
          <Field label="Engine" help="Blank uses the project's translation engine. Needs an LLM engine.">
            <input value={engine} onChange={(e) => setEngine(e.target.value)} />
          </Field>
          <Field label="Model" help="Blank uses the engine's default model.">
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </Field>
        </div>
      </Section>
      <div className="actions">
        <button
          type="button"
          disabled={busy || !state || tooFew}
          onClick={() => run(learnStyle(dramaId, { engine: engine.trim() || null, model: model.trim() || null }))}
        >
          {busy ? 'Working…' : profile ? 'Learn again' : 'Learn my style'}
        </button>
        {profile && (
          <ConfirmButton name="learned style" label="Reset…" verb="reset" busy={busy} onConfirm={() => run(resetStyle(dramaId))} />
        )}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
