import { useEffect, useState } from 'react'

import { getStyle, learnStyle, resetStyle, restoreStyle, setStyleApplied } from '../../../../api/reviewExtras'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { humanize } from '../../../../components/labels'
import { usePcOnly } from '../../../../hooks/usePcOnly'
import type { StyleState } from '../../../../types/reviewExtras'
import { styleSummary } from './aiExtrasLogic'
import { AI_ENGINE_LABEL } from '../../../../helpText'

interface Props {
  dramaId: number
  reloads: number
}

// Learn my style: one LLM call over every recorded edit; the learned
// preferences go into future translations (series-wide, else global) unless
// paused here. Reset and restore are PC-only; learning or pausing the
// library-wide profile (a drama with no series) is PC-only too, while a
// series profile can be learned or paused from anywhere.
export function AiExtrasStyle({ dramaId, reloads }: Props) {
  const pc = usePcOnly()
  const [state, setState] = useState<StyleState | null>(null)
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [resetError, setResetError] = useState<unknown>(null)

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

  // On a failure the stored profile may have changed meanwhile (a 409 when it
  // was reset during learning): re-read it so the panel isn't stale.
  const run = (p: Promise<StyleState>, onError: (e: unknown) => void = setError) => {
    setBusy(true)
    setError(null)
    setResetError(null)
    p.then(setState, (e) => {
      onError(e)
      getStyle(dramaId).then(setState, () => undefined)
    }).finally(() => setBusy(false))
  }

  const profile = state?.profile ?? null
  const tooFew = !!state && state.edit_count < state.min_samples
  const scope = state?.scope === 'series' ? 'this series' : 'all projects'
  const globalLocked = pc === 'remote' && state?.scope === 'global'
  const previous = state?.history?.[0] ?? null

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
          <Field label="Use in future translations" help="Off pauses the learned style without forgetting it.">
            <Toggle checked={profile.applied} disabled={busy || globalLocked} onChange={(next) => run(setStyleApplied(dramaId, next))} />
          </Field>
        </div>
      )}
      <Section title="Advanced" summary={`${engine ? humanize('engine', engine) : 'Default engine'} · ${model || 'default model'}`}>
        <div className="review-edit-row">
          <Field label={AI_ENGINE_LABEL} help="Blank uses the project's translation engine. Needs an LLM engine.">
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
          className={buttonClass('primary')}
          disabled={busy || !state || tooFew || globalLocked}
          onClick={() => run(learnStyle(dramaId, { engine: engine.trim() || null, model: model.trim() || null }))}
        >
          {busy ? 'Working…' : profile ? 'Learn again' : 'Learn my style'}
        </button>
        {profile && pc !== 'remote' && (
          <ConfirmButton name="learned style" label="Reset…" verb="reset" busy={busy} onConfirm={() => run(resetStyle(dramaId), setResetError)} />
        )}
        {previous && pc !== 'remote' && (
          <button
            type="button"
            className={buttonClass()}
            disabled={busy}
            title={`${previous.preference_count} preference(s)${previous.summary ? `: ${previous.summary}` : ''}`}
            onClick={() => run(restoreStyle(dramaId, 0), setResetError)}
          >
            Restore previous
          </button>
        )}
      </div>
      {globalLocked && <p className="muted">Learning or pausing the style for all projects is PC only.</p>}
      {(profile || previous) && pc === 'remote' && <p className="muted">Resetting or restoring the learned style is PC only.</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ErrorBanner error={resetError} describe={{ pcOnly: true }} onDismiss={() => setResetError(null)} />
    </Section>
  )
}
