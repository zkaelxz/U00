import { useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import {
  applyWorkflowTier,
  getTranslateConfig,
  getTranslateEstimate,
  saveTranslatePreset,
  startTranslateRun,
} from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { TranslateRunConfig, TranslateRunEstimate, WorkflowTierApplied } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import {
  applyTierToForm,
  buildEstimateParams,
  buildPresetBody,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  initialForm,
  loadPresetStart,
  MAX_FALLBACKS,
  monthSpendText,
  reflectAvailable,
  PRESET_NAME_MAX,
  validatePresetName,
  validateRun, type RunForm,
} from '../translateForm'
import { BulkBatchesPanel } from './BulkBatchesPanel'
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
  if (f.genre_notes !== base.genre_notes) parts.push(f.genre_notes ? 'genre notes on' : 'genre notes off')
  if (f.female_pronouns !== base.female_pronouns) parts.push(f.female_pronouns ? 'she/her default' : 'no she/her default')
  if (f.fallbacks.length) parts.push(`${f.fallbacks.length} fallback${f.fallbacks.length === 1 ? '' : 's'}`)
  if (f.reflect) parts.push('reflect')
  if (f.bulk) parts.push('bulk')
  if (f.force) parts.push('re-translate existing')
  return parts.length ? parts.join(' · ') : 'defaults'
}

function appliedText(t: WorkflowTierApplied): string {
  const model = t.engine_model ? ` (${t.engine_model})` : ''
  const qc = t.auto_qc ? ' Auto QC before export: on; run it from the Export stage.' : ''
  return `Applied ${t.label}: ${t.translation_engine}${model}, Reflect ${t.reflect ? 'on' : 'off'}.${qc} Nothing has started.`
}

// Parity X02: Streamlit's "Starting tier" + "Apply tier". Saves the tier's
// engine on the drama and fills the form; never starts a run.
function TierPicker({ config, onApplied }: { config: TranslateRunConfig; onApplied: (t: WorkflowTierApplied) => void }) {
  const { dramaId } = useStage()
  const tiers = config.workflow_tiers ?? []
  const [tier, setTier] = useState(() => (tiers.some((t) => t.key === 'standard') ? 'standard' : (tiers[0]?.key ?? '')))
  const [applied, setApplied] = useState<WorkflowTierApplied | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  if (!tiers.length) return null
  const apply = () => {
    setPending(true)
    applyWorkflowTier(dramaId, tier).then(
      (t) => {
        setError(null)
        setApplied(t)
        onApplied(t)
      },
      setError,
    ).finally(() => setPending(false))
  }
  return (
    <div className="check-row translate-tier">
      <Field label="Starting tier" help="Sets the engine, model and Reflect together. Draft: DeepSeek, no Reflect. Standard: Claude Sonnet, no Reflect. Release: Claude Opus, Reflect on, Auto QC on. Everything stays editable afterward, and nothing starts until you press Translate.">
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
          {tiers.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
      </Field>
      <button type="button" disabled={pending || !tier} onClick={apply}>Apply tier</button>
      {applied && <span className="muted" role="status">{appliedText(applied)}</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

// Parity X22: Streamlit's "Save as preset". Captures engine, model, style,
// locale and the two toggles. A taken name asks before replacing it.
function SavePreset({ f, defaultEngine }: { f: RunForm; defaultEngine: string }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [taken, setTaken] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const save = (overwrite: boolean) => {
    const bad = validatePresetName(name)
    setProblem(bad)
    if (bad) return
    setPending(true)
    setSaved(null)
    saveTranslatePreset(buildPresetBody(f, defaultEngine, name, overwrite)).then(
      (r) => {
        setError(null)
        setTaken(null)
        setSaved(`${r.replaced ? 'Replaced' : 'Saved'} preset "${r.preset.name}".`)
        setOpen(false)
        setName('')
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 409) {
          setError(null)
          setTaken(name.trim())
        } else setError(e)
      },
    ).finally(() => setPending(false))
  }
  return (
    <div className="advanced-wide">
      {!open ? (
        <div className="check-row">
          <button type="button" onClick={() => { setOpen(true); setSaved(null) }}>Save as preset…</button>
          <span className="muted">Saves the engine, model, style, locale and the two guidance toggles for any drama.</span>
          {saved && <span role="status">{saved}</span>}
        </div>
      ) : (
        <div className="fallback-row">
          <Field label="Preset name">
            <input
              type="text"
              maxLength={PRESET_NAME_MAX}
              value={name}
              autoFocus
              onChange={(e) => { setName(e.target.value); setTaken(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') save(false) }}
            />
          </Field>
          <button type="button" className="primary" disabled={pending} onClick={() => save(false)}>Save preset</button>
          <button type="button" onClick={() => { setOpen(false); setTaken(null); setProblem(null) }}>Cancel</button>
        </div>
      )}
      {open && problem && <p className="error" role="alert">{problem}</p>}
      {open && taken && (
        <p className="error" role="alert">
          A preset named "{taken}" already exists.{' '}
          <button type="button" disabled={pending} onClick={() => save(true)}>Replace it</button>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

function RunPanel({ config, onStarted, busy }: { config: TranslateRunConfig; onStarted: (id: string) => void; busy: boolean }) {
  const { dramaId } = useStage()
  const [base] = useState<RunForm>(() => initialForm(config, loadPresetStart(dramaId)))
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
      <TierPicker config={config} onApplied={(t) => setF((s) => applyTierToForm(s, t, config))} />
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
            help={`Stop this run at this many dollars; blank means no cap. ${monthSpendText(config.month_spend, config.monthly_cap_usd)}`}
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
          </div>
          <div className="advanced-wide check-row">
            <label className="inline" title="Pronoun clarity, kinship-term nuance, and not softening romantic content.">
              <input type="checkbox" checked={f.genre_notes} onChange={(e) => set('genre_notes', e.target.checked)} />{' '}
              Include baihe/GL genre guidance
            </label>
            <label className="inline" title="Spoken Mandarin does not distinguish he/she; for a mostly female cast, default an ambiguous pronoun to she/her. A character's own pronouns always win.">
              <input type="checkbox" checked={f.female_pronouns} onChange={(e) => set('female_pronouns', e.target.checked)} />{' '}
              Default ambiguous pronouns to she/her
            </label>
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
          <SavePreset f={f} defaultEngine={config.translation_engine} />
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
      {config && (
        <BulkBatchesPanel supported={config.bulk_supported_engines.length > 0} reloadKey={reloads} />
      )}
      <GlossaryPanel />
      <CharactersPanel />
    </div>
  )
}
