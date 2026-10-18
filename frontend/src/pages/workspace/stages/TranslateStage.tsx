import { useEffect, useId, useRef, useState, type MutableRefObject } from 'react'

import { ApiError } from '../../../api/client'
import { updateDramaMetadata } from '../../../api/library'
import {
  getTranslateConfig,
  getTranslateEstimate,
  saveTranslatePreset,
  startTranslateRun,
} from '../../../api/translateStage'
import { ButtonLink } from '../../../components/Button'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { RetryBanner } from '../../../components/RetryBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { ModelSelect } from '../../../components/ModelSelect'
import { Section } from '../../../components/Section'
import { Tabs } from '../../../components/Tabs'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import { isBulkJobId, translateJobIds } from '../stageJobIds'
import { parseRoute, routeHref } from '../../../router'
import type {
  TranslatePresetApplied,
  TranslateRunConfig,
  TranslateRunEstimate,
  WorkflowTierApplied,
} from '../../../types/translateStage'
import { browserStorage } from '../../../hooks/usePersistedState'
import { hashWithTab } from '../mediaTabs'
import {
  TRANSLATE_TAB_PREFIX,
  TRANSLATE_TABS,
  isTranslateTab,
  readTranslateTab,
  resolveTranslateTab,
  tabForFocus,
  writeTranslateTab,
  type TranslateTab,
} from '../translateTabs'
import { useStage } from '../StageContext'
import { MediaTabPanel } from './MediaTabPanel'
import {
  applyPresetToForm,
  applyStartToForm,
  applyTierToForm,
  buildEstimateParams,
  estimateKeyFor,
  buildPresetBody,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  FALLBACK_KIND_MESSAGE,
  fallbackKindMismatch,
  fallbackOptions,
  initialForm,
  loadPresetStart,
  restoreRunOptions,
  runOptionsDraft,
  MAX_FALLBACKS,
  monthSpendText,
  cloudModelNotice,
  ollamaWarning,
  reflectAvailable,
  thinkingApplies,
  thinkingHelp,
  translateButtonLabel,
  PRESET_NAME_MAX,
  styleGuidance,
  validatePresetName,
  validateRun,
  defaultBatchSize,
  batchOverride,
  batchSizeRange,
  contextRange,
  withPresetEngine,
  withSavedEngine,
  TRANSLATE_DRAFT_STAGE,
  type RunForm,
} from '../translateForm'
import { useStageDraft } from '../../../hooks/useStageDraft'
import { StyleField } from './StartFromPicker'
import { BulkBatchesPanel } from './BulkBatchesPanel'
import { CharactersPanel } from './CharactersPanel'
import { GlossaryPanel } from './GlossaryPanel'
import { GlossaryRetranslateAction } from './GlossaryRetranslate'
import { engineNotesHelp, engineOptionLabel } from '../../../api/translate'
import { FailedBatchesNotice, GlossaryFlaggedNotice } from './FollowupNotices'
import { GlossaryScanBanner } from './GlossaryScanBanner'
import { GlossaryReview } from './GlossaryReview'
import type { GlossarySource } from './glossaryExtract'
import { scanBanner } from './glossaryScan'
import { useGlossaryScans } from './useGlossaryRun'
import { JobPanel } from './JobPanel'
import { NovelFilePanel } from './NovelFilePanel'
import { SourceChangedWarning } from './SourceChangedWarning'
import { EngineGateNote } from './EngineGateNote'
import { translateBlocker } from './stageBlockers'
import { useEngineGate } from './useEngineGate'
import './translate.css'
import { AI_ENGINE_LABEL, NO_KEY_ENGINES_HELP } from '../../../helpText'

// Where to look when the chosen engine runs on this PC and does not answer.
const LOCAL_ENGINE_HINT = 'Ollama is not answering on this PC. See Diagnostics > Models for what is installed.'

const REVIEW_GLOSSARY_NOTE = 'Translate will first scan the transcript for glossary terms and show them for your approval, then translate.'
const REVIEW_GLOSSARY_HELP = `Changes what the Translate button does. ${REVIEW_GLOSSARY_NOTE} The scan uses the engine saved for this title.`

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
function advancedSummary(f: RunForm, base: RunForm, reviewFirst: boolean): string {
  const parts: string[] = []
  if (reviewFirst) parts.push('review glossary first')
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

// "Save as profile". Captures every field of the form but the English variant
// (see buildPresetBody). A taken name asks before replacing it.
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
        setSaved(`${r.replaced ? 'Replaced' : 'Saved'} profile "${r.preset.name}".`)
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
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { setOpen(true); setSaved(null) }}>Save as profile…</button>
          <span className="muted">Saves this form (engine, model, style, the guidance toggles and the options below) to start any title from. The English variant and the glossary are not saved.</span>
          {saved && <span role="status">{saved}</span>}
        </div>
      ) : (
        <div className="fallback-row">
          <Field label="Profile name">
            <input
              type="text"
              maxLength={PRESET_NAME_MAX}
              value={name}
              autoFocus
              onChange={(e) => { setName(e.target.value); setTaken(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') save(false) }}
            />
          </Field>
          <button type="button" className="primary" disabled={pending} onClick={() => save(false)}>Save profile</button>
          <button type="button" className={buttonClass('ghost')} onClick={() => { setOpen(false); setTaken(null); setProblem(null) }}>Cancel</button>
        </div>
      )}
      {open && problem && <p className="error" role="alert">{problem}</p>}
      {open && taken && (
        <p className="error" role="alert">
          A profile named "{taken}" already exists.{' '}
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
  onForm,
  busy,
  bulkPending,
  retryRef,
}: {
  config: TranslateRunConfig
  onStarted: (id: string) => void
  onTierApplied: (t: WorkflowTierApplied) => void
  onPresetApplied: (p: TranslatePresetApplied) => void
  onRecheckOllama: () => Promise<void>
  // The Glossary's Re-translate tab runs with this form's settings.
  onForm: (f: RunForm) => void
  busy: boolean
  // The running job is a bulk batch (the busy reason points to Bulk batches).
  bulkPending: boolean
  // Set to the Translate action, so the stage's Last run card can run it again with this form.
  retryRef: MutableRefObject<(() => void) | null>
}) {
  const { dramaId, drama } = useStage()
  const [base] = useState<RunForm>(() => applyStartToForm(initialForm(config, loadPresetStart(dramaId)), config.translate_start, config))
  // What the owner last chose for this title, restored after a reload.
  const { raw: rawDraft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, TRANSLATE_DRAFT_STAGE, {})
  const [restored] = useState(() => restoreRunOptions(rawDraft, base, config))
  // Parity X28: review proposed glossary terms before the run starts.
  // reviewing counts presses (0 = closed) so each press extracts afresh.
  const [reviewFirst, setReviewFirst] = useState(restored.reviewFirst && !!drama.series_id)
  const [reviewing, setReviewing] = useState(0)
  const [reviewNote, setReviewNote] = useState<string | null>(null)
  const canReview = !!drama.series_id
  // A scan outlives this page: one started before leaving the stage (or reloading) is shown here
  // whether or not the review option is on. With the option already on at mount the review opens
  // by itself; otherwise the banner offers it.
  const scans = useGlossaryScans(dramaId)
  const banner = scans.loaded ? scanBanner(scans.scans) : null
  const [reviewOnMount] = useState(reviewFirst && canReview)
  const [resumeSource, setResumeSource] = useState<GlossarySource | undefined>(undefined)
  const [resumeOpen, setResumeOpen] = useState(false)
  const openResume = (source: GlossarySource) => {
    setResumeSource(source)
    setResumeOpen(true)
    setReviewing((n) => n + 1)
  }
  const autoResumed = useRef(false)
  useEffect(() => {
    if (!banner || !reviewOnMount || autoResumed.current) return
    autoResumed.current = true
    openResume(banner.source)
  }, [banner, reviewOnMount])
  const [f, setF] = useState<RunForm>(restored.form)
  useEffect(() => onForm(f), [onForm, f])
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [estimate, setEstimate] = useState<{ key: string; value: TranslateRunEstimate } | null>(null)
  const [estimateError, setEstimateError] = useState<unknown>(null)
  // Set while the start request is in flight, so a double click can't send two runs.
  const [starting, setStarting] = useState(false)
  const set = <K extends keyof RunForm>(k: K, v: RunForm[K]) => setF((s) => ({ ...s, [k]: v }))
  useEffect(() => {
    saveDraft(runOptionsDraft({ form: f, baseEngine: config.translation_engine, reviewFirst }, defaultBatchSize(config, f)))
  }, [saveDraft, f, config, reviewFirst])
  // Back to the title's saved engine and the Settings defaults; the draft for this title is dropped.
  const resetToDefaults = () => {
    clearDraft()
    setF(base)
    setReviewFirst(false)
    setProblem(null)
  }
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
  // Per title only (not part of a preset or a run): saved as soon as it changes.
  const [bySentence, setBySentence] = useState<boolean>(config.translate_by_sentence ?? false)
  const [bySentenceSaved, setBySentenceSaved] = useState<boolean | null>(null)
  const saveBySentence = (v: boolean) => {
    setBySentence(v)
    setBySentenceSaved(null)
    updateDramaMetadata(dramaId, { translate_by_sentence: v }).then(
      () => setBySentenceSaved(true),
      () => setBySentenceSaved(false),
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
  const scanFirst = reviewFirst && canReview
  const lineCount = f.force && f.forceConfirmed ? config.line_count : config.untranslated_count
  const engineBatch = defaultBatchSize(config, f)
  const batchOverridden = batchOverride(config, f)
  const guidance = styleGuidance(config, f.style_preset)
  const blocker = translateBlocker(config.line_count, config.untranslated_count, f.force, f.forceConfirmed)
  const gate = useEngineGate(config.engines, effEngine, config.ollama_reachable)
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
    const key = JSON.stringify(params)
    getTranslateEstimate(dramaId, params).then(
      (e) => {
        setEstimateError(null)
        setEstimate({ key, value: e })
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
      setResumeOpen(false)
      setResumeSource(undefined)
      setReviewing((n) => n + 1)
      return
    }
    if (starting) return
    setStarting(true)
    startTranslateRun(dramaId, buildRunBody(f)).then((r) => {
      setError(null)
      onStarted(r.job_id)
    }, setError).finally(() => setStarting(false))
  }
  useEffect(() => {
    retryRef.current = () => start()
  })

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
        {ollamaWarning(effEngine, config.ollama_reachable) && (
          <p className="muted translate-basics-wide" data-testid="local-engine-hint">{LOCAL_ENGINE_HINT}</p>
        )}
        <ModelSelect engine={engine} value={f.model} onChange={(m) => set('model', m)} />
        <StyleField
          config={config}
          style={f.style_preset}
          onStyleChange={(key) => set('style_preset', key)}
          onTierApplied={(t) => {
            setF((s) => applyTierToForm(s, t, config))
            onTierApplied(t)
          }}
          onPresetApplied={(p) => {
            setF((s) => applyPresetToForm(s, p, config))
            onPresetApplied(p)
          }}
        />
        <Field label="English variant" help="Spelling for the translation: US, UK or Australian English.">
          <select value={f.locale} onChange={(e) => set('locale', e.target.value)}>
            {config.locales.map((l) => <option key={l} value={l}>{humanize('locale', l)}</option>)}
          </select>
        </Field>
      </div>
      {guidance && (
        <details className="style-guidance">
          <summary>What this style asks for</summary>
          <p className="muted" data-testid="style-guidance">{guidance}</p>
        </details>
      )}
      {cloudModelNotice(engine, f.model) && (
        <p className="warn" role="note" data-testid="cloud-model-notice">{cloudModelNotice(engine, f.model)}</p>
      )}
      {ollamaWarning(effEngine, config.ollama_reachable) && <OllamaNotice onRecheck={onRecheckOllama} />}
      {gate && gate.id !== 'engine_ollama' && <div id="translate-gate"><EngineGateNote gate={gate} /></div>}
      <div className="translate-go">
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={busy || starting || reviewing > 0 || blocker !== null || gate !== null}
          aria-describedby={blocker ? 'translate-blocker' : gate ? 'translate-gate' : busy ? 'translate-busy' : undefined}
          onClick={() => start()}
        >
          {translateButtonLabel(scanFirst, lineCount)}
        </button>
        {blocker && (
          <p className="stage-blocker" id="translate-blocker" data-testid="translate-blocker">
            {blocker.kind === 'no-lines' && (
              <>
                <span>Still needed: lines to translate.</span>
                <ButtonLink variant="ghost" size="sm" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
                  Go to Media
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
        <button type="button" className={buttonClass('secondary')} onClick={runEstimate}>Estimate cost</button>
        <GlossaryRetranslateAction inline f={f} busy={busy} onStarted={onStarted} />
        {/* aria-live rather than role=status: the stage already has one status line, and this region must stay mounted to be announced. */}
        <span aria-live="polite">{estimate && estimate.key === estimateKeyFor(f) && <EstimateView e={estimate.value} />}</span>
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
      {reviewing > 0 && (
        <GlossaryReview
          key={reviewing}
          resume={resumeOpen}
          resumeSource={resumeSource}
          engine={config.translation_engine}
          onStart={(note) => {
            setReviewing(0)
            setReviewNote(note)
            start(true)
          }}
          onCancel={() => setReviewing(0)}
        />
      )}
      {banner && reviewing === 0 && <GlossaryScanBanner banner={banner} onOpen={() => openResume(banner.source)} />}
      {reviewNote && <p className="muted" role="status">Glossary: {reviewNote}</p>}
      {busy && (
        <p className="muted" id="translate-busy" data-testid="translate-busy">
          {bulkPending
            ? 'A bulk batch is waiting on the provider, which can take hours. To run a normal translation now, cancel it under Bulk batches below.'
            : 'A translate job is running. Progress is shown above.'}
        </p>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={estimateError} onDismiss={() => setEstimateError(null)} />
      {error instanceof ApiError && error.status === 409 && (
        <p className="error" role="alert">A translate job is already running for this title.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Section storageKey="translate.advanced" title="More options" summary={advancedSummary(f, base, reviewFirst)}>
        <div className="advanced-grid">
          <div className="advanced-wide">
            <Field label="Style note" help="Optional extra instruction for this run only.">
              <textarea rows={2} value={f.style_note} onChange={(e) => set('style_note', e.target.value)} />
            </Field>
          </div>
          <div>
            <Field label="Batch size" unit="lines" help={`Lines sent per request, ${batchSizeRange()}. Leave blank for the default for this engine (${engineBatch}).`}>
              <input type="number" value={f.batch_size} placeholder={String(engineBatch)} onChange={(e) => set('batch_size', e.target.value)} />
            </Field>
          </div>
          <Field label="Context before" unit="lines" help={`Earlier lines sent as context, ${contextRange()}.`}>
            <input type="number" value={f.context_window} onChange={(e) => set('context_window', e.target.value)} />
          </Field>
          <Field label="Context ahead" unit="lines" help={`Following lines sent as context, ${contextRange()}.`}>
            <input type="number" value={f.context_window_ahead} onChange={(e) => set('context_window_ahead', e.target.value)} />
          </Field>
          <Field
            label="Cost cap"
            unit="$"
            help={`Stop this run at this many dollars; blank means no cap. ${monthSpendText(config.month_spend, config.monthly_cap_usd)}`}
          >
            <input type="number" min={0} step="0.01" value={f.cost_cap} onChange={(e) => set('cost_cap', e.target.value)} />
          </Field>
          {batchOverridden && (
            <p className="muted batch-size-note advanced-wide" data-testid="batch-size-note">
              Batch size: Using {batchOverridden.value} (set by you). Default for this engine is {batchOverridden.engineDefault}.{' '}
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => set('batch_size', '')}>Use default</button>
            </p>
          )}
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
            {canReview && (
              <>
                <Field
                  label="Review glossary before translating"
                  help={`${REVIEW_GLOSSARY_HELP} A glossary mistake repeats on every line.`}
                >
                  <Toggle checked={reviewFirst} disabled={reviewing > 0} onChange={setReviewFirst} />
                </Field>
                {reviewFirst && <p className="muted" data-testid="review-glossary-note">{REVIEW_GLOSSARY_NOTE}</p>}
              </>
            )}
            {reflectAvailable(effEngine) && (
              <Field
                label="Reflect"
                help={`Three passes: translate, critique, then revise. Slower and costs more; not available for translation-only engines.${!canReflect && !f.reflect ? ' Not with Bulk on this engine.' : ''}`}
              >
                <Toggle checked={f.reflect} disabled={!canReflect && !f.reflect} onChange={(v) => set('reflect', v)} />
              </Field>
            )}
            <Field label="Think harder on tricky text (slower, costs more)" help={thinkingHelp(effEngine, f.reflect, config.thinking_switch_engines, f.fallbacks)}>
              <Toggle checked={f.thinking && thinkingApplies(effEngine, f.reflect, config.thinking_switch_engines, f.fallbacks)} disabled={!thinkingApplies(effEngine, f.reflect, config.thinking_switch_engines, f.fallbacks)} onChange={(v) => set('thinking', v)} />
            </Field>
            {bulkAvailable(effEngine, config.bulk_supported_engines) && (
              <Field
                label="Bulk"
                help={`Send the whole title as one discounted batch (Claude/Gemini batch API or DeepSeek off-peak). Results can take up to 24 hours; needs no line selection or fallbacks.${!canBulk && !f.bulk ? ' Not with Reflect on this engine.' : ''}`}
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
              help={`Mandarin 他/她 sound the same, so the translator is told to write she/her where a pronoun is ambiguous, and lines that still come out he/him for a speaker with no he/him set are asked again once, then flagged in Review. It applies to lines translated after you save it: turn on Re-translate existing to redo lines that already have English. It does not override characters set to he/him, nor an honorific or context that says male.${savedNote}`}
            >
              <Toggle checked={f.female_pronouns} onChange={(v) => saveToggle('female_pronouns', v)} />
            </Field>
            <Field
              label="Translate by sentence"
              help="Translate whole sentences, then split the English across the short subtitle lines. Slower and uses more tokens; helps when lines were split a lot."
            >
              <Toggle checked={bySentence} onChange={saveBySentence} />
            </Field>
            {bySentenceSaved !== null && (
              <span className="muted" aria-live="polite" data-testid="by-sentence-saved">
                {bySentenceSaved ? 'Saved for this title; every later run uses it.' : 'Could not save this choice for the title; it will not apply to later runs.'}
              </span>
            )}
            {savedNote && <span className="muted" aria-live="polite" data-testid="toggle-saved">{savedNote.trim()}</span>}
            <Field label="Re-translate existing" help="Also replace English that is already there. You confirm it under the Translate button; a snapshot is saved first.">
              <Toggle checked={f.force} onChange={(v) => setF((s) => ({ ...s, force: v, forceConfirmed: false }))} />
            </Field>
          </div>
          <SavePreset f={f} defaultEngine={config.translation_engine} />
          <div className="advanced-wide actions">
            <button type="button" className={buttonClass('ghost')} onClick={resetToDefaults}>Reset to defaults</button>
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
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(translateJobIds(dramaId), adoptJob)
  const retry = useRef<(() => void) | null>(null)
  const [reloads, setReloads] = useState(0)

  // The sub-tab: a link's ?tab= first, then the panel a ?focus= link points at, then the one this title was
  // last left on.
  const linkedTab = () => {
    const r = parseRoute(window.location.hash)
    if (r.name !== 'drama' || r.id !== dramaId) return undefined
    return isTranslateTab(r.tab) ? r.tab : tabForFocus(r.focus) ?? undefined
  }
  const [wantedTab, setWantedTab] = useState<TranslateTab | null>(() => linkedTab() ?? readTranslateTab(browserStorage(), dramaId))
  // A link followed while this stage is open (a glossary blocker, "Build the glossary") moves the tab too.
  useEffect(() => {
    const follow = () => {
      const next = linkedTab()
      if (next) setWantedTab(next)
    }
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId])
  const tab = resolveTranslateTab(wantedTab)
  const selectTab = (next: TranslateTab) => {
    setWantedTab(next)
    writeTranslateTab(browserStorage(), dramaId, next)
    // Keep the address true to the tab shown, so a reload or Back lands here and not on the tab first linked.
    // replaceState sends no hashchange, so this does not come back through the listener above.
    try {
      window.history.replaceState(null, '', hashWithTab(window.location.hash, next))
    } catch {
      // the address stays as it was
    }
  }

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
  const [runForm, setRunForm] = useState<RunForm | null>(null)
  // X24 "Check again": re-read the config but take only the reachability flag,
  // so the loaded config (and the form built from it) stays as it is.
  const recheckOllama = () =>
    getTranslateConfig(dramaId).then((c) =>
      setConfig((prev) => (prev ? { ...prev, ollama_reachable: c.ollama_reachable ?? null } : prev)))

  // A live job shows above every tab so a run started here stays in view from the Glossary tab; the finished
  // run (Last run) belongs to the Translate tab.
  const showJob = jobId !== null || tab === 'translate'

  return (
    <div className="stage-translate">
      {config ? (
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      ) : (
        <RetryBanner error={error} what="the translate settings" onRetry={() => { setError(null); setReloads((n) => n + 1) }} />
      )}
      <SourceChangedWarning dramaId={dramaId} reloadKey={reloads} />
      {showJob && (
        <JobPanel
          jobId={jobId}
          job={job}
          pollError={pollError}
          lastRun={{ dramaId, ids: translateJobIds(dramaId), retryFor: () => (config ? () => retry.current?.() : null) }}
        />
      )}
      <Tabs
        label="Translate sections"
        idPrefix={TRANSLATE_TAB_PREFIX}
        tabs={TRANSLATE_TABS}
        active={tab}
        onSelect={selectTab}
      />
      <MediaTabPanel id="translate" active={tab} prefix={TRANSLATE_TAB_PREFIX} className="translate-tabpanel">
        {config && (
          <>
            <FailedBatchesNotice
              config={config}
              form={runForm}
              busy={busy}
              onStarted={setJobId}
              onDismissed={() => setConfig((c) => (c ? { ...c, last_translate_errors: null } : c))}
            />
            <GlossaryFlaggedNotice config={config} busy={busy} onStarted={setJobId} />
          </>
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
            onForm={setRunForm}
            retryRef={retry}
          />
        )}
        <BulkBatchesPanel reloadKey={reloads} />
      </MediaTabPanel>
      <MediaTabPanel id="glossary" active={tab} prefix={TRANSLATE_TAB_PREFIX} className="translate-tabpanel">
        <GlossaryPanel
          defaultOpen
          focusReady={config !== null || error !== null}
          retranslate={config && runForm ? { f: runForm, busy, onStarted: setJobId } : null}
        />
      </MediaTabPanel>
      <MediaTabPanel id="characters" active={tab} prefix={TRANSLATE_TAB_PREFIX} className="translate-tabpanel">
        <CharactersPanel defaultOpen focusReady={config !== null || error !== null} />
      </MediaTabPanel>
      <MediaTabPanel id="novel" active={tab} prefix={TRANSLATE_TAB_PREFIX} className="translate-tabpanel">
        <NovelFilePanel kind="reference" defaultOpen busy={busy} onChanged={() => setReloads((n) => n + 1)} />
      </MediaTabPanel>
    </div>
  )
}
