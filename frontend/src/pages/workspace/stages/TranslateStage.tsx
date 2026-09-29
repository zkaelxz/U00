import { useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getTranslateConfig, getTranslateEstimate, startTranslateRun } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { useJob } from '../../../hooks/useJob'
import type { TranslateRunConfig, TranslateRunEstimate } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { buildEstimateParams, buildRunBody, initialForm, MAX_FALLBACKS, validateRun, type RunForm } from '../translateForm'
import { CharactersPanel } from './CharactersPanel'
import { GlossaryPanel } from './GlossaryPanel'
import { JobPanel } from './JobPanel'
import './translateStage.css'

function EstimateView({ e }: { e: TranslateRunEstimate }) {
  const cost = e.free ? 'free' : e.estimated_usd === null ? 'unknown' : `about $${e.estimated_usd.toFixed(2)}`
  return (
    <div data-testid="estimate">
      <p>
        {e.target_line_count} line(s) to translate with {e.engine}
        {e.model ? ` (${e.model})` : ''}: {cost}.
      </p>
      {e.effective_cap_usd !== null && <p className="muted">Cap in effect: ${e.effective_cap_usd.toFixed(2)}</p>}
      {e.monthly_refusal && (
        <p className="error" role="alert">The monthly cap would be exceeded, so this run would be refused.</p>
      )}
      {e.estimate_above_cap && !e.monthly_refusal && (
        <p className="error" role="alert">The estimate is above the cap, so the run would stop early.</p>
      )}
    </div>
  )
}

function RunPanel({ config, onStarted, busy }: { config: TranslateRunConfig; onStarted: (id: string) => void; busy: boolean }) {
  const { dramaId } = useStage()
  const [f, setF] = useState<RunForm>(() => initialForm(config))
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [estimateError, setEstimateError] = useState<unknown>(null)
  const set = <K extends keyof RunForm>(k: K, v: RunForm[K]) => setF((s) => ({ ...s, [k]: v }))

  const engine = config.engines.find((e) => e.name === (f.engine || config.translation_engine))
  const models = engine?.models ?? []
  const engineLabel = (name: string) => {
    const e = config.engines.find((x) => x.name === name)
    return e ? `${e.label} (key ${e.key_configured ? 'configured' : 'not configured'})` : name
  }

  const runEstimate = () => {
    const params = buildEstimateParams(f)
    setEstimate(null)
    if (!params) return setProblem('Cost cap must be a number of dollars, 0 or more.')
    setProblem(null)
    getTranslateEstimate(dramaId, params).then(
      (e) => {
        setEstimateError(null)
        setEstimate(e)
      },
      setEstimateError,
    )
  }

  const start = () => {
    const bad = validateRun(f, config.translation_engine)
    setProblem(bad)
    if (bad) return
    startTranslateRun(dramaId, buildRunBody(f)).then((r) => {
      setError(null)
      onStarted(r.job_id)
    }, setError)
  }

  return (
    <section className="panel" aria-label="Translate run">
      <h3>Translate</h3>
      <p className="muted" data-testid="translate-counts">
        {config.untranslated_count} of {config.line_count} lines have no English yet. Spend this month: $
        {config.month_spend.toFixed(2)} of ${config.monthly_cap_usd.toFixed(2)}.
      </p>
      <fieldset className="form-grid">
        <legend>Options</legend>
        <label>
          Engine
          <select
            aria-label="Engine"
            value={f.engine}
            onChange={(e) => setF((s) => ({ ...s, engine: e.target.value, model: '' }))}
          >
            <option value="">Default ({engineLabel(config.translation_engine)})</option>
            {config.engines.map((e) => (
              <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>
            ))}
          </select>
        </label>
        {models.length > 0 && (
          <label>
            Model
            <select value={f.model} onChange={(e) => set('model', e.target.value)}>
              <option value="">Engine default</option>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}
        <label>
          Style preset
          <select value={f.style_preset} onChange={(e) => set('style_preset', e.target.value)}>
            {config.style_presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </label>
        <label>
          Locale
          <select value={f.locale} onChange={(e) => set('locale', e.target.value)}>
            {config.locales.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <label>
          Style note (optional)
          <textarea rows={2} value={f.style_note} onChange={(e) => set('style_note', e.target.value)} />
        </label>
        <label>
          Batch size (1-200)
          <input type="number" value={f.batch_size} onChange={(e) => set('batch_size', e.target.value)} />
        </label>
        <label>
          Context window before (0-100)
          <input type="number" value={f.context_window} onChange={(e) => set('context_window', e.target.value)} />
        </label>
        <label>
          Context window ahead (0-100)
          <input type="number" value={f.context_window_ahead} onChange={(e) => set('context_window_ahead', e.target.value)} />
        </label>
        <label>
          Cost cap for this run in dollars (blank = none)
          <input type="number" min={0} step="0.01" value={f.cost_cap} onChange={(e) => set('cost_cap', e.target.value)} />
        </label>
        <div>
          <strong>Fallback engines</strong> <span className="muted">(tried in order if the engine fails, up to {MAX_FALLBACKS})</span>
          {f.fallbacks.map((fb, i) => (
            <div className="fallback-row" key={i}>
              <select
                aria-label={`Fallback engine ${i + 1}`}
                value={fb}
                onChange={(e) => set('fallbacks', f.fallbacks.map((x, j) => (j === i ? e.target.value : x)))}
              >
                <option value="">Choose an engine</option>
                {config.engines.map((e) => <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>)}
              </select>
              <button type="button" onClick={() => set('fallbacks', f.fallbacks.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
          ))}
          {f.fallbacks.length < MAX_FALLBACKS && (
            <div>
              <button type="button" onClick={() => set('fallbacks', [...f.fallbacks, ''])}>Add fallback engine</button>
            </div>
          )}
        </div>
        <label className="inline">
          <input
            type="checkbox"
            checked={f.force}
            onChange={(e) => setF((s) => ({ ...s, force: e.target.checked, forceConfirmed: false }))}
          />{' '}
          Re-translate lines that already have English
        </label>
        {f.force && (
          <label className="inline">
            <input
              type="checkbox"
              checked={f.forceConfirmed}
              onChange={(e) => set('forceConfirmed', e.target.checked)}
            />{' '}
            I understand this replaces existing English text (a snapshot is saved first)
          </label>
        )}
      </fieldset>
      {problem && <p className="error" role="alert">{problem}</p>}
      <div className="actions">
        <button type="button" onClick={runEstimate}>Estimate cost</button>
        <button type="button" disabled={busy} onClick={start}>Start translation</button>
      </div>
      {estimate && <EstimateView e={estimate} />}
      <ErrorBanner error={estimateError} onDismiss={() => setEstimateError(null)} />
      {error instanceof ApiError && error.status === 409 && (
        <p className="error" role="alert">A translate job is already running for this drama.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}

export default function TranslateStage() {
  const { dramaId, onJobDone } = useStage()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    getTranslateConfig(dramaId).then(
      (c) => !cancelled && setConfig(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const { job, done, error: pollError } = useJob(jobId, {
    onDone: () => {
      onJobDone()
      setReloads((n) => n + 1)
    },
  })
  const busy = jobId !== null && !done && !pollError

  return (
    <div className="stage-translate">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {config && <RunPanel config={config} busy={busy} onStarted={setJobId} />}
      {jobId && <JobPanel job={job} pollError={pollError} />}
      <GlossaryPanel />
      <CharactersPanel />
    </div>
  )
}
