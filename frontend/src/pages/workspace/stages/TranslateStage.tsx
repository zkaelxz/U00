import { useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getTranslateConfig, getTranslateEstimate, resumeBulkTranslations, startTranslateRun } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { BulkResumeResult, TranslateRunConfig, TranslateRunEstimate } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import {
  buildEstimateParams,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  initialForm,
  MAX_FALLBACKS,
  reflectAvailable,
  validateRun, type RunForm,
} from '../translateForm'
import { CharactersPanel } from './CharactersPanel'
import { GlossaryPanel } from './GlossaryPanel'
import { JobPanel } from './JobPanel'
import './translate.css'

function EstimateView({ e }: { e: TranslateRunEstimate }) {
  const cost = e.free ? 'free' : e.estimated_usd === null ? 'unknown' : `about $${e.estimated_usd.toFixed(2)}`
  const cap = e.effective_cap_usd !== null ? ` · cap $${e.effective_cap_usd.toFixed(2)}` : ''
  return (
    <span className="translate-estimate" data-testid="estimate">
      {e.target_line_count} line(s) with {e.engine}
      {e.model ? ` (${e.model})` : ''}: {cost}
      {cap}
      {e.monthly_refusal && (
        <span className="error" role="alert">The monthly cap would be exceeded, so this run would be refused.</span>
      )}
      {e.estimate_above_cap && !e.monthly_refusal && (
        <span className="error" role="alert">The estimate is above the cap, so the run would stop early.</span>
      )}
    </span>
  )
}

// One line naming only the Advanced values that differ from their defaults.
function advancedSummary(f: RunForm, base: RunForm): string {
  const parts: string[] = []
  if (f.style_note.trim()) parts.push('style note')
  if (f.batch_size !== base.batch_size) parts.push(`batch ${f.batch_size}`)
  if (f.context_window !== base.context_window || f.context_window_ahead !== base.context_window_ahead) {
    parts.push(`context ${f.context_window}/${f.context_window_ahead}`)
  }
  if (f.cost_cap.trim()) parts.push(`cap $${f.cost_cap.trim()}`)
  if (f.fallbacks.length) parts.push(`${f.fallbacks.length} fallback${f.fallbacks.length === 1 ? '' : 's'}`)
  if (f.reflect) parts.push('reflect')
  if (f.bulk) parts.push('bulk')
  if (f.force) parts.push('re-translate existing')
  return parts.length ? parts.join(' · ') : 'defaults'
}

function RunPanel({ config, onStarted, busy }: { config: TranslateRunConfig; onStarted: (id: string) => void; busy: boolean }) {
  const { dramaId } = useStage()
  const [base] = useState<RunForm>(() => initialForm(config))
  const [f, setF] = useState<RunForm>(base)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [estimateError, setEstimateError] = useState<unknown>(null)
  const set = <K extends keyof RunForm>(k: K, v: RunForm[K]) => setF((s) => ({ ...s, [k]: v }))

  const engine = config.engines.find((e) => e.name === (f.engine || config.translation_engine))
  const models = engine?.models ?? []
  const engineLabel = (name: string) => {
    const e = config.engines.find((x) => x.name === name)
    return e ? `${e.label}${e.key_configured ? '' : ' (no key)'}` : name
  }
  const effEngine = f.engine || config.translation_engine
  const canReflect = reflectAvailable(effEngine) && !(f.bulk && !bulkReflectAvailable(effEngine, config.bulk_supported_engines))
  const canBulk = bulkAvailable(effEngine, config.bulk_supported_engines) && !(f.reflect && !bulkReflectAvailable(effEngine, config.bulk_supported_engines))
  const [resumed, setResumed] = useState<BulkResumeResult | null>(null)
  const resume = () => resumeBulkTranslations(dramaId).then((r) => { setError(null); setResumed(r) }, setError)
  const lineCount = f.force && f.forceConfirmed ? config.line_count : config.untranslated_count

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
    const bad = validateRun(f, config.translation_engine, config.bulk_supported_engines)
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
      <div className="translate-basics">
        <Field label="Engine" help="Which service translates. The default comes from Settings; engines marked (no key) cannot run.">
          <select value={f.engine} onChange={(e) => setF((s) => ({ ...s, engine: e.target.value, model: '', reflect: false, bulk: false }))}>
            <option value="">Default ({engineLabel(config.translation_engine)})</option>
            {config.engines.map((e) => (
              <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>
            ))}
          </select>
        </Field>
        {models.length > 0 && (
          <Field label="Model">
            <select value={f.model} onChange={(e) => set('model', e.target.value)}>
              <option value="">Engine default</option>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
        )}
        <Field label="Style" help="Style preset: what the translator is asked to sound like.">
          <select value={f.style_preset} onChange={(e) => set('style_preset', e.target.value)}>
            {config.style_presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Locale" help="English variant, for example en-US or en-GB spelling.">
          <select value={f.locale} onChange={(e) => set('locale', e.target.value)}>
            {config.locales.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </Field>
      </div>
      <div className="translate-go">
        <button type="button" className="primary" disabled={busy} onClick={start}>
          Translate {lineCount} line{lineCount === 1 ? '' : 's'}
        </button>
        <button type="button" className="link" onClick={runEstimate}>Estimate cost</button>
        {estimate && <EstimateView e={estimate} />}
      </div>
      {busy && <p className="muted">A translate job is running. Progress is shown below.</p>}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={estimateError} onDismiss={() => setEstimateError(null)} />
      {error instanceof ApiError && error.status === 409 && (
        <p className="error" role="alert">A translate job is already running for this drama.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Section storageKey="translate.advanced" title="Advanced" summary={advancedSummary(f, base)}>
        <div className="advanced-grid">
          <div className="advanced-wide">
            <Field label="Style note" help="Optional extra instruction for this run only.">
              <textarea rows={2} value={f.style_note} onChange={(e) => set('style_note', e.target.value)} />
            </Field>
          </div>
          <Field label="Batch size" unit="lines" help="Lines sent per request, 1 to 200.">
            <input type="number" value={f.batch_size} onChange={(e) => set('batch_size', e.target.value)} />
          </Field>
          <Field label="Context before" unit="lines" help="Earlier lines sent as context, 0 to 100.">
            <input type="number" value={f.context_window} onChange={(e) => set('context_window', e.target.value)} />
          </Field>
          <Field label="Context ahead" unit="lines" help="Following lines sent as context, 0 to 100.">
            <input type="number" value={f.context_window_ahead} onChange={(e) => set('context_window_ahead', e.target.value)} />
          </Field>
          <Field
            label="Cost cap"
            unit="$"
            help={`Stop this run at this many dollars; blank means no cap. Spend this month: $${config.month_spend.toFixed(2)} of $${config.monthly_cap_usd.toFixed(2)}.`}
          >
            <input type="number" min={0} step="0.01" value={f.cost_cap} onChange={(e) => set('cost_cap', e.target.value)} />
          </Field>
          <div className="advanced-wide">
            <div className="field-label-row">
              <strong>Fallback engines</strong>
              <span className="muted">tried in order if the engine fails, up to {MAX_FALLBACKS}</span>
            </div>
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
              <div className="fallback-row">
                <button type="button" onClick={() => set('fallbacks', [...f.fallbacks, ''])}>Add fallback engine</button>
              </div>
            )}
          </div>
          <div className="advanced-wide check-row">
            {reflectAvailable(effEngine) && (
              <Field label="Reflect" help="Three passes: translate, critique, then revise. Slower and costs more; not available for translation-only engines.">
                <input type="checkbox" checked={f.reflect} disabled={!canReflect && !f.reflect} onChange={(e) => set('reflect', e.target.checked)} />
              </Field>
            )}
            {bulkAvailable(effEngine, config.bulk_supported_engines) && (
              <Field label="Bulk" help="Send the whole drama as one discounted batch (Claude/Gemini batch API or DeepSeek off-peak). Results can take up to 24 hours; needs no line selection or fallbacks.">
                <input type="checkbox" checked={f.bulk} disabled={!canBulk && !f.bulk} onChange={(e) => set('bulk', e.target.checked)} />
              </Field>
            )}
            {config.bulk_supported_engines.length > 0 && (
              <>
                <button type="button" className="link" onClick={resume}>Resume pending batches</button>
                {resumed && (
                  <span className="muted" data-testid="bulk-resume">
                    {resumed.jobs.length === 0
                      ? 'No pending batches.'
                      : resumed.jobs.map((j) => `#${j.bulk_job_id} ${j.state}`).join(', ')}
                  </span>
                )}
              </>
            )}
          </div>
          <div className="advanced-wide check-row">
            <label className="inline">
              <input
                type="checkbox"
                checked={f.force}
                onChange={(e) => setF((s) => ({ ...s, force: e.target.checked, forceConfirmed: false }))}
              />{' '}
              Re-translate existing
            </label>
            {f.force && (
              <label className="inline">
                <input
                  type="checkbox"
                  checked={f.forceConfirmed}
                  onChange={(e) => set('forceConfirmed', e.target.checked)}
                />{' '}
                I understand this replaces existing English (a snapshot is saved first)
              </label>
            )}
          </div>
        </div>
      </Section>
    </section>
  )
}

export default function TranslateStage() {
  const { dramaId, onJobDone } = useStage()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey] = useJobRun()
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
    runKey,
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
