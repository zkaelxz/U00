import { useEffect, useId, useRef, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getPresets, updateDramaMetadata } from '../../../api/library'
import {
  applyTranslatePreset,
  applyWorkflowTier,
  dismissTranslateErrors,
  getTranslateConfig,
  getTranslateEstimate,
  saveTranslatePreset,
  startTranslateRun,
} from '../../../api/translateStage'
import { ButtonLink } from '../../../components/Button'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { ModelSelect } from '../../../components/ModelSelect'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import { isBulkJobId, translateJobIds } from '../stageJobIds'
import { routeHref } from '../../../router'
import type { LibraryPreset } from '../../../types/library'
import type {
  TranslatePresetApplied,
  TranslateRunConfig,
  TranslateRunEstimate,
  WorkflowTierApplied,
} from '../../../types/translateStage'
import { useStage } from '../StageContext'
import {
  applyPresetToForm,
  applyTierToForm,
  buildEstimateParams,
  buildPresetBody,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  FALLBACK_KIND_MESSAGE,
  failedBatches,
  fallbackKindMismatch,
  fallbackOptions,
  initialForm,
  lineRanges,
  loadPresetStart,
  MAX_FALLBACKS,
  monthSpendText,
  cloudModelNotice,
  ollamaWarning,
  reflectAvailable,
  thinkingApplies,
  thinkingHelp,
  PRESET_NAME_MAX,
  savePresetStart,
  styleGuidance,
  validatePresetName,
  validateRun,
  withPresetEngine,
  withSavedEngine,
  type RunForm,
} from '../translateForm'
import { BulkBatchesPanel } from './BulkBatchesPanel'
import { CharactersPanel } from './CharactersPanel'
import { GlossaryPanel } from './GlossaryPanel'
import { engineNotesHelp, engineOptionLabel } from '../../../api/translate'
import { GlossaryRetranslate } from './GlossaryRetranslate'
import { GlossaryReview } from './GlossaryReview'
import { JobPanel } from './JobPanel'
import { NovelFilePanel } from './NovelFilePanel'
import { translateBlocker } from './stageBlockers'
import './translate.css'
import { AI_ENGINE_LABEL, NOTHING_STARTS_HELP, NO_KEY_ENGINES_HELP } from '../../../helpText'

function EstimateView({ e }: { e: TranslateRunEstimate }) {
  const cost = e.free ? 'free' : e.estimated_usd === null ? 'unknown'
    : e.estimate_is_lower_bound ? `at least $${e.estimated_usd.toFixed(2)} (thinking adds hidden output, so the real cost is higher)`
      : `about $${e.estimated_usd.toFixed(2)}`
  const cap = e.effective_cap_usd !== null ? ` · cap $${e.effective_cap_usd.toFixed(2)}` : ''
  return (
    <span className="translate-estimate" data-testid="estimate">
      {e.target_line_count} line(s) with {humanize('engine', e.engine)}
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
  if (f.thinking !== base.thinking) parts.push(f.thinking ? 'thinking on' : 'thinking off')
  if (f.force) parts.push('re-translate existing')
  return parts.length ? parts.join(' · ') : 'defaults'
}

function appliedText(t: WorkflowTierApplied): string {
  const model = t.engine_model ? ` (${t.engine_model})` : ''
  const name = t.tier.charAt(0).toUpperCase() + t.tier.slice(1)
  const qc = t.auto_qc ? ` ${name} recommends Auto QC; run it from the Export stage.` : ''
  return `Applied ${t.label}: ${humanize('engine', t.translation_engine)}${model}, Reflect ${t.reflect ? 'on' : 'off'}.${qc} Nothing has started.`
}

// "Starting tier" + "Apply tier". Saves the tier's engine on the drama and
// fills the form; never starts a run.
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
      <Field label="Starting tier" help={`Sets engine, model and Reflect together (Draft: DeepSeek; Standard: Claude Sonnet; Release: Claude Opus with Reflect and Auto QC), and stays editable. ${NOTHING_STARTS_HELP}`}>
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
          {tiers.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary', 'sm')} disabled={pending || !tier} onClick={apply}>Apply tier</button>
      {applied && <span className="muted" role="status">{appliedText(applied)}</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

// "Apply a preset" on an existing drama. Saves the
// preset's engine on the drama, fills the form and keeps its values for later
// visits (as a preset chosen at creation does); never starts a run.
function PresetPicker({ onApplied }: { onApplied: (p: TranslatePresetApplied) => void }) {
  const { dramaId } = useStage()
  const [presets, setPresets] = useState<LibraryPreset[] | null>(null)
  const [picked, setPicked] = useState('')
  const [applied, setApplied] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  useEffect(() => {
    let cancelled = false
    getPresets().then(
      (r) => !cancelled && setPresets(r.items),
      () => !cancelled && setPresets([]), // the list is optional here; the Library shows its own error
    )
    return () => {
      cancelled = true
    }
  }, [])
  if (!presets?.length) return null
  const apply = () => {
    setPending(true)
    applyTranslatePreset(dramaId, Number(picked))
      .then(
        (p) => {
          setError(null)
          savePresetStart(dramaId, p)
          setApplied(`Applied preset "${p.name}". Nothing has started.`)
          onApplied(p)
        },
        setError,
      )
      .finally(() => setPending(false))
  }
  return (
    <div className="check-row translate-tier">
      <Field label="Saved preset" help={`Fills in engine, model, style, English variant and guidance toggles from a saved preset. ${NOTHING_STARTS_HELP} Manage presets in the Library.`}>
        <select value={picked} onChange={(e) => { setPicked(e.target.value); setApplied(null) }}>
          <option value="">Choose a preset</option>
          {presets.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary', 'sm')} disabled={pending || !picked} onClick={apply}>Apply preset</button>
      {applied && <span className="muted" role="status">{applied}</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

// "Save as preset". Captures engine, model, style,
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
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { setOpen(true); setSaved(null) }}>Save as preset…</button>
          <span className="muted">Saves the engine, model, style, English variant and the two guidance toggles for any drama.</span>
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
          <button type="button" className={buttonClass('ghost')} onClick={() => { setOpen(false); setTaken(null); setProblem(null) }}>Cancel</button>
        </div>
      )}
      {open && problem && <p className="error" role="alert">{problem}</p>}
      {open && taken && (
        <p className="error" role="alert">
          A preset named "{taken}" already exists.{' '}
          <button type="button" className={buttonClass('danger', 'sm')} disabled={pending} onClick={() => save(true)}>Replace it</button>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

// "Can't reach Ollama" warning. A warning only: the run stays startable, so
// the server's own error is the final word. No URL is shown: the
// server only sends a boolean. "Check again" re-reads just that flag.
function OllamaNotice({ onRecheck }: { onRecheck: () => Promise<void> }) {
  const [pending, setPending] = useState(false)
  const [rechecked, setRechecked] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const recheck = () => {
    setPending(true)
    setError(null)
    onRecheck().then(
      () => setRechecked(true),
      setError,
    ).finally(() => setPending(false))
  }
  return (
    <div className="translate-ollama" data-testid="ollama-warning">
      <span className="warn" role="status">Can't reach Ollama on this PC. Is it running? Start Ollama, then check again.</span>
      <button type="button" className={buttonClass('ghost', 'sm')} disabled={pending} onClick={recheck}>Check again</button>
      {rechecked && !pending && <span className="muted">Still no answer.</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

function RunPanel({
  config,
  onStarted,
  onTierApplied,
  onPresetApplied,
  onRecheckOllama,
  busy,
  bulkPending,
}: {
  config: TranslateRunConfig
  onStarted: (id: string) => void
  onTierApplied: (t: WorkflowTierApplied) => void
  onPresetApplied: (p: TranslatePresetApplied) => void
  onRecheckOllama: () => Promise<void>
  busy: boolean
  // The running job is a bulk batch (the busy reason points to Bulk batches).
  bulkPending: boolean
}) {
  const { dramaId, drama } = useStage()
  const [base] = useState<RunForm>(() => initialForm(config, loadPresetStart(dramaId)))
  // Parity X28: review proposed glossary terms before the run starts.
  // reviewing counts presses (0 = closed) so each press extracts afresh.
  const [reviewFirst, setReviewFirst] = useState(false)
  const [reviewing, setReviewing] = useState(0)
  const [reviewNote, setReviewNote] = useState<string | null>(null)
  const canReview = !!drama.series_id
  const [f, setF] = useState<RunForm>(base)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [estimateError, setEstimateError] = useState<unknown>(null)
  const set = <K extends keyof RunForm>(k: K, v: RunForm[K]) => setF((s) => ({ ...s, [k]: v }))
  // The two prompt toggles are saved for the title as soon as they change, so
  // every later run (retries, glossary re-translation, AI line actions, the
  // CLI) uses what is shown here, not a default.
  const [toggleSaved, setToggleSaved] = useState<boolean | null>(null)
  const saveToggle = (key: 'female_pronouns' | 'genre_notes', v: boolean) => {
    set(key, v)
    setToggleSaved(null)
    const body = key === 'female_pronouns' ? { default_female_pronouns: v } : { include_genre_notes: v }
    updateDramaMetadata(dramaId, body).then(
      () => setToggleSaved(true),
      () => setToggleSaved(false),
    )
  }
  const savedNote =
    toggleSaved === true ? ' Saved for this title; every later run uses it.'
    : toggleSaved === false ? ' Could not save this choice for the title; it applies to the next run only.'
    : config.default_female_pronouns != null || config.include_genre_notes != null
      ? ' Saved for this title; every run uses it.'
      : ''

  const engine = config.engines.find((e) => e.name === (f.engine || config.translation_engine))
  const engineLabel = (name: string) => {
    const e = config.engines.find((x) => x.name === name)
    return e ? engineOptionLabel(e) : name
  }
  const effEngine = f.engine || config.translation_engine
  const canReflect = reflectAvailable(effEngine) && !(f.bulk && !bulkReflectAvailable(effEngine, config.bulk_supported_engines))
  const canBulk = bulkAvailable(effEngine, config.bulk_supported_engines) && !(f.reflect && !bulkReflectAvailable(effEngine, config.bulk_supported_engines))
  const lineCount = f.force && f.forceConfirmed ? config.line_count : config.untranslated_count
  const guidance = styleGuidance(config, f.style_preset)
  const blocker = translateBlocker(config.line_count, config.untranslated_count, f.force, f.forceConfirmed)
  // "Re-translate existing…" unmounts itself; hand focus to the confirm box that replaces it.
  const focusAck = useRef(false)
  // Fallbacks: same kind as the main engine, never with Reflect or Bulk.
  const fallbackIds = useId()
  const engineNames = config.engines.map((e) => e.name)
  const fallbackOff = f.reflect || f.bulk
  const fallbackMismatch = fallbackKindMismatch(effEngine, f.fallbacks)
  const canAddFallback = f.fallbacks.length < MAX_FALLBACKS && !fallbackOff
    && fallbackOptions(engineNames, effEngine, f.fallbacks, -1).length > f.fallbacks.filter((e) => !e).length

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

  const start = (afterReview = false) => {
    const bad = validateRun(f, config.translation_engine, config.bulk_supported_engines)
    setProblem(bad)
    if (bad) return
    if (reviewFirst && canReview && !afterReview) {
      setReviewNote(null)
      setReviewing((n) => n + 1)
      return
    }
    startTranslateRun(dramaId, buildRunBody(f)).then((r) => {
      setError(null)
      onStarted(r.job_id)
    }, setError)
  }

  return (
    <section className="panel" aria-label="Translate run">
      <h3>Translate</h3>
      <div className="translate-basics">
        <Field label={AI_ENGINE_LABEL} help={`Which service translates. The default comes from Settings. ${NO_KEY_ENGINES_HELP} ${engineNotesHelp(config.engines)}`}>
          <select className="engine-select" value={f.engine} onChange={(e) => setF((s) => ({ ...s, engine: e.target.value, model: '', reflect: false, bulk: false }))}>
            <option value="">Default ({engineLabel(config.translation_engine)})</option>
            {config.engines.map((e) => (
              <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>
            ))}
          </select>
        </Field>
        <ModelSelect engine={engine} value={f.model} onChange={(m) => set('model', m)} />
        <Field label="Style" help="Style preset: what the translator is asked to sound like.">
          <select value={f.style_preset} onChange={(e) => set('style_preset', e.target.value)}>
            {config.style_presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="English variant" help="Spelling for the translation: US, UK or Australian English.">
          <select value={f.locale} onChange={(e) => set('locale', e.target.value)}>
            {config.locales.map((l) => <option key={l} value={l}>{humanize('locale', l)}</option>)}
          </select>
        </Field>
      </div>
      {guidance && (
        <details className="style-guidance">
          <summary>What this style asks the translator for</summary>
          <p className="muted" data-testid="style-guidance">{guidance}</p>
        </details>
      )}
      {cloudModelNotice(engine, f.model) && (
        <p className="warn" role="note" data-testid="cloud-model-notice">{cloudModelNotice(engine, f.model)}</p>
      )}
      {ollamaWarning(effEngine, config.ollama_reachable) && <OllamaNotice onRecheck={onRecheckOllama} />}
      <div className="translate-go">
        <button
          type="button"
          className="primary"
          disabled={busy || reviewing > 0 || blocker !== null}
          aria-describedby={blocker ? 'translate-blocker' : busy ? 'translate-busy' : undefined}
          onClick={() => start()}
        >
          Translate {lineCount} line{lineCount === 1 ? '' : 's'}
        </button>
        {blocker && (
          <p className="stage-blocker" id="translate-blocker" data-testid="translate-blocker">
            {blocker.kind === 'no-lines' && (
              <>
                <span>Still needed: lines to translate.</span>
                <ButtonLink variant="ghost" size="sm" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
                  Go to Source
                </ButtonLink>
              </>
            )}
            {blocker.kind === 'all-translated' && (
              <>
                <span>All {blocker.total} line{blocker.total === 1 ? ' has' : 's have'} English.</span>
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  onClick={() => {
                    focusAck.current = true
                    setF((s) => ({ ...s, force: true, forceConfirmed: false }))
                  }}
                >
                  Re-translate existing…
                </button>
              </>
            )}
            {blocker.kind === 'confirm-force' && <span>Still needed: confirm replacing the existing English below.</span>}
          </p>
        )}
        <button type="button" className={buttonClass('ghost')} onClick={runEstimate}>Estimate cost</button>
        {estimate && <EstimateView e={estimate} />}
      </div>
      {f.force && (
        <label className="inline stage-ack">
          <input
            type="checkbox"
            ref={(el) => {
              if (el && focusAck.current) {
                focusAck.current = false
                el.focus()
              }
            }}
            checked={f.forceConfirmed}
            onChange={(e) => set('forceConfirmed', e.target.checked)} />{' '}
          I understand this replaces existing English (a snapshot is saved first)
        </label>
      )}
      {canReview && <GlossaryRetranslate f={f} busy={busy} onStarted={onStarted} />}
      {canReview && (
        <div className="setting-list">
          <Field
            label="Review glossary before translating"
            help="Before the run starts, proposes glossary terms from the attached novel (or the source lines) with this drama's engine, so you can fix them first. A glossary mistake repeats on every line."
          >
            <Toggle checked={reviewFirst} disabled={reviewing > 0} onChange={setReviewFirst} />
          </Field>
        </div>
      )}
      {reviewing > 0 && (
        <GlossaryReview
          key={reviewing}
          onStart={(note) => {
            setReviewing(0)
            setReviewNote(note)
            start(true)
          }}
          onCancel={() => setReviewing(0)}
        />
      )}
      {reviewNote && <p className="muted" role="status">Glossary: {reviewNote}</p>}
      {busy && (
        <p className="muted" id="translate-busy" data-testid="translate-busy">
          {bulkPending
            ? 'A bulk batch is waiting on the provider, which can take hours. To run a normal translation now, cancel it under Bulk batches below.'
            : 'A translate job is running. Progress is shown below.'}
        </p>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={estimateError} onDismiss={() => setEstimateError(null)} />
      {error instanceof ApiError && error.status === 409 && (
        <p className="error" role="alert">A translate job is already running for this drama.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <TierPicker
        config={config}
        onApplied={(t) => {
          setF((s) => applyTierToForm(s, t, config))
          onTierApplied(t)
        }}
      />
      <PresetPicker
        onApplied={(p) => {
          setF((s) => applyPresetToForm(s, p, config))
          onPresetApplied(p)
        }}
      />
      <Section storageKey="translate.advanced" title="Advanced" summary={advancedSummary(f, base)}>
        <div className="advanced-grid">
          <div className="advanced-wide">
            <Field label="Style note" help="Optional extra instruction for this run only.">
              <textarea rows={2} value={f.style_note} onChange={(e) => set('style_note', e.target.value)} />
            </Field>
          </div>
          <Field label="Batch size" unit="lines" help="Lines sent per request, 1 to 60.">
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
          <div
            className="advanced-wide fallback-group"
            role="group"
            aria-labelledby={`${fallbackIds}-title`}
            aria-describedby={`${fallbackIds}-rule`}
            data-testid="fallback-engines"
          >
            <div className="field-label-row">
              <strong id={`${fallbackIds}-title`}>Fallback engines</strong>
            </div>
            <p className="muted fallback-rule" id={`${fallbackIds}-rule`}>
              Up to {MAX_FALLBACKS}, tried in order only after the main engine keeps failing (it retries first). Same kind
              as the main engine: AI with AI, translation-only with translation-only. Not with Reflect or Bulk.
            </p>
            {fallbackOff && (
              <p className="muted fallback-rule" role="status">
                Off while {f.reflect ? 'Reflect' : 'Bulk'} is on{f.fallbacks.length ? '; remove these to run' : ''}.
              </p>
            )}
            {f.fallbacks.map((fb, i) => {
              const options = fallbackOptions(engineNames, effEngine, f.fallbacks, i)
              return (
                <div className="fallback-row" key={i}>
                  <select
                    aria-label={`Fallback engine ${i + 1}`}
                    value={fb}
                    disabled={fallbackOff}
                    onChange={(e) => set('fallbacks', f.fallbacks.map((x, j) => (j === i ? e.target.value : x)))}
                  >
                    <option value="">Choose an engine</option>
                    {fb && !options.includes(fb) && <option value={fb}>{engineLabel(fb)} (can't be used here)</option>}
                    {options.map((name) => <option key={name} value={name}>{engineLabel(name)}</option>)}
                  </select>
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => set('fallbacks', f.fallbacks.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
              )
            })}
            {fallbackMismatch && <p className="error" role="alert">{FALLBACK_KIND_MESSAGE}</p>}
            {canAddFallback && (
              <div className="fallback-row">
                <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => set('fallbacks', [...f.fallbacks, ''])}>Add fallback engine</button>
              </div>
            )}
          </div>
          <div className="advanced-wide setting-list">
            {reflectAvailable(effEngine) && (
              <Field
                label="Reflect"
                help={`Three passes: translate, critique, then revise. Slower and costs more; not available for translation-only engines.${!canReflect && !f.reflect ? ' Not with Bulk on this engine.' : ''}`}
              >
                <Toggle checked={f.reflect} disabled={!canReflect && !f.reflect} onChange={(v) => set('reflect', v)} />
              </Field>
            )}
            <Field label="Think harder on tricky text (slower, costs more)" help={thinkingHelp(effEngine, f.reflect, config.thinking_switch_engines)}>
              <Toggle checked={f.thinking && thinkingApplies(effEngine, f.reflect, config.thinking_switch_engines)} disabled={!thinkingApplies(effEngine, f.reflect, config.thinking_switch_engines)} onChange={(v) => set('thinking', v)} />
            </Field>
            {bulkAvailable(effEngine, config.bulk_supported_engines) && (
              <Field
                label="Bulk"
                help={`Send the whole drama as one discounted batch (Claude/Gemini batch API or DeepSeek off-peak). Results can take up to 24 hours; needs no line selection or fallbacks.${!canBulk && !f.bulk ? ' Not with Reflect on this engine.' : ''}`}
              >
                <Toggle checked={f.bulk} disabled={!canBulk && !f.bulk} onChange={(v) => set('bulk', v)} />
              </Field>
            )}
            <Field
              label="Include baihe/GL genre guidance"
              help={`Adds the baihe notes to the prompt: both leads are women, keep 姐姐/妹妹-style address, do not soften romance. It does not by itself turn he into she; use the she/her default for that.${savedNote}`}
            >
              <Toggle checked={f.genre_notes} onChange={(v) => saveToggle('genre_notes', v)} />
            </Field>
            <Field
              label="Default ambiguous pronouns to she/her"
              help={`A soft default, not a rule: Mandarin 他/她 sound the same, so where a pronoun is ambiguous the translator is told to write she/her. Context, an honorific, or a character's own pronouns (set under Characters) still win; a character set to he/him stays he/him.${savedNote}`}
            >
              <Toggle checked={f.female_pronouns} onChange={(v) => saveToggle('female_pronouns', v)} />
            </Field>
            {savedNote && <span className="muted" aria-live="polite" data-testid="toggle-saved">{savedNote.trim()}</span>}
            <Field label="Re-translate existing" help="Also replace English that is already there. You confirm it under the Translate button; a snapshot is saved first.">
              <Toggle checked={f.force} onChange={(v) => setF((s) => ({ ...s, force: v, forceConfirmed: false }))} />
            </Field>
          </div>
          <SavePreset f={f} defaultEngine={config.translation_engine} />
        </div>
      </Section>
    </section>
  )
}

// Warning about the last run's failed batches, with
// Dismiss (clears only that record; the lines stay untranslated, so running
// Translate again retries just those).
function FailedBatchesNotice({ config, onDismissed }: { config: TranslateRunConfig; onDismissed: () => void }) {
  const { dramaId } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const failed = failedBatches(config.last_translate_errors)
  if (!failed) return null
  const lines = failed.lineNumbers.length
  const dismiss = () => {
    setPending(true)
    setError(null)
    dismissTranslateErrors(dramaId).then(
      () => onDismissed(),
      (e: unknown) => {
        setError(e)
        setPending(false)
      },
    )
  }
  return (
    <section className="panel translate-failed" aria-label="Failed batches" data-testid="failed-batches">
      <p className="warn">
        The last translation run had {failed.batches} failed batch{failed.batches === 1 ? '' : 'es'}
        {lines > 0 && <>; line{lines === 1 ? '' : 's'} {lineRanges(failed.lineNumbers)} {lines === 1 ? 'is' : 'are'} still untranslated</>}.
        {' '}Translate again to retry only the missing lines; lines already translated are skipped.
      </p>
      {failed.reasons.length > 0 && (
        <ul className="muted translate-failed-reasons">
          {failed.reasons.slice(0, 3).map((r) => <li key={r}>{r}</li>)}
          {failed.reasons.length > 3 && <li>and {failed.reasons.length - 3} more</li>}
        </ul>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <button type="button" onClick={dismiss} disabled={pending}>Dismiss notice</button>
    </section>
  )
}

export default function TranslateStage() {
  const { dramaId, onJobDone } = useStage()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(translateJobIds(dramaId), adoptJob)
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
  // X24 "Check again": re-read the config but take only the reachability flag,
  // so the loaded config (and the form built from it) stays as it is.
  const recheckOllama = () =>
    getTranslateConfig(dramaId).then((c) =>
      setConfig((prev) => (prev ? { ...prev, ollama_reachable: c.ollama_reachable ?? null } : prev)))

  return (
    <div className="stage-translate">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {config && (
        <FailedBatchesNotice
          config={config}
          onDismissed={() => setConfig((c) => (c ? { ...c, last_translate_errors: null } : c))}
        />
      )}
      {!config && !error && (
        <div className="skeleton-block translate-skeleton" role="status" aria-busy="true" aria-label="Loading translate options" />
      )}
      {config && (
        <RunPanel
          config={config}
          busy={busy}
          bulkPending={isBulkJobId(jobId)}
          onStarted={setJobId}
          onTierApplied={(t) => setConfig((c) => (c ? withSavedEngine(c, t) : c))}
          onPresetApplied={(p) => setConfig((c) => (c ? withPresetEngine(c, p) : c))}
          onRecheckOllama={recheckOllama}
        />
      )}
      {jobId && <JobPanel job={job} pollError={pollError} />}
      <BulkBatchesPanel reloadKey={reloads} />
      <NovelFilePanel kind="reference" busy={busy} onChanged={() => setReloads((n) => n + 1)} />
      <GlossaryPanel focusReady={config !== null || error !== null} />
      <CharactersPanel focusReady={config !== null || error !== null} />
    </div>
  )
}
