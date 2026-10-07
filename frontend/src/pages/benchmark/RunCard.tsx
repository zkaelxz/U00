import { useEffect, useMemo, useRef, useState } from 'react'

import {
  estimateBenchmark, startBenchmarkRun, type BenchmarkConfig, type BenchmarkEstimate,
  type BenchmarkOptions, type BenchmarkRunStarted, type BenchmarkSet, type BenchmarkStage, type BenchmarkTier,
} from '../../api/benchmark'
import { modelOptionLabel } from '../../api/translate'
import { ButtonLink } from '../../components/Button'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { usePersistedState } from '../../hooks/usePersistedState'
import { routeHref } from '../../router'
import type { JobRecord } from '../../types/jobs'
import { engineOptionLabel } from '../translatePage'
import { BenchSection } from './BenchSection'
import { NO_CASES_HINT } from './benchmarkHelp'
import {
  OCR_LABELS, STAGE_LABELS, TIER_LABELS, casesInSelection, comparePrefill, configLabel, defaultConfig, enginesMissingKey,
  estimateKey, formatCost, parseCompareParam, plainError, restoreConfigs, runRequestBody, selectionProblems, setOptions,
  runningStatus, startState, type ComparePrefill, type RunSelection,
} from './benchmarkForm'

type Props = {
  options: BenchmarkOptions
  sets: BenchmarkSet[]
  pcRemote: boolean
  job: JobRecord | null
  running: boolean
  onStarted: (res: BenchmarkRunStarted) => void
  onStop: () => void
  // The raw ?compare= value of a "Compare in Benchmark Lab" link (Model health).
  compare?: string
}

const STAGES: BenchmarkStage[] = ['translation', 'transcription', 'ocr']

/** "Run a benchmark": what to run, on which cases; estimate first, then Start. */
export function RunCard({ options, sets, pcRemote, job, running, onStarted, onStop, compare }: Props) {
  const [stage, setStage] = usePersistedState<string>('benchmark.stage', 'translation')
  const st: BenchmarkStage = STAGES.includes(stage as BenchmarkStage) ? (stage as BenchmarkStage) : 'translation'
  const [tier, setTier] = usePersistedState<string>('benchmark.tier', '')
  const [setName, setSetName] = usePersistedState<string>('benchmark.set', '')
  const [promptVersion, setPromptVersion] = usePersistedState<string>('benchmark.promptVersion', '')
  const [savedConfigs, setSavedConfigs] = usePersistedState<Record<string, unknown>>('benchmark.configs', {})
  const [label, setLabel] = useState('')
  const [estimate, setEstimate] = useState<BenchmarkEstimate | null>(null)
  const [estimateFor, setEstimateFor] = useState<string | null>(null)
  const [busy, setBusy] = useState<'estimate' | 'start' | null>(null)
  // An error belongs to the selection it was about (keyed like the estimate).
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null)

  const configs = useMemo(() => restoreConfigs(st, savedConfigs[st], options), [st, savedConfigs, options])
  const setConfigs = (next: BenchmarkConfig[]) => setSavedConfigs({ ...savedConfigs, [st]: next })

  // A compare link sets up a translation run of those engines once (and is
  // remembered like any other pick); the query then leaves the address bar
  // so a reload doesn't undo later changes.
  const [prefill, setPrefill] = useState<ComparePrefill | null>(null)
  const appliedCompare = useRef<string | null>(null)
  useEffect(() => {
    if (!compare || appliedCompare.current === compare) return
    appliedCompare.current = compare
    const p = comparePrefill(parseCompareParam(compare), options)
    if (p.configs.length) {
      setStage('translation')
      setSavedConfigs({ ...savedConfigs, translation: p.configs })
    }
    setPrefill(p)
    if (window.location.hash.startsWith('#/benchmark?')) window.history.replaceState(window.history.state, '', '#/benchmark')
  }, [compare, options, savedConfigs, setStage, setSavedConfigs])

  const tierValue = (options.tiers as string[]).includes(tier) ? (tier as BenchmarkTier) : ''
  const setChoices = setOptions(sets, st, tierValue)
  const setValue = setChoices.includes(setName) ? setName : ''
  const sel: RunSelection = { stage: st, configs, tier: tierValue, setName: setValue, label, promptVersion }
  const key = estimateKey(sel)
  const shown = estimate && estimateFor === key ? estimate : null
  const missingKeys = enginesMissingKey(sel, options.translation_engines)
  const problems = selectionProblems(sel, options.max_configs)
  const start = startState({ sel, maxConfigs: options.max_configs, estimate, estimateFor, pcRemote, running, missingKeys })
  const caseCount = casesInSelection(sets, st, tierValue, setValue)

  const error = failure && failure.key === key ? failure.text : null
  const setError = (text: string | null) => setFailure(text ? { key, text } : null)

  const runEstimate = async () => {
    setBusy('estimate')
    setError(null)
    try {
      const est = await estimateBenchmark(runRequestBody(sel))
      setEstimate(est)
      setEstimateFor(key)
    } catch (e) {
      setError(plainError(e))
    } finally {
      setBusy(null)
    }
  }

  const runStart = async () => {
    setBusy('start')
    setError(null)
    try {
      const res = await startBenchmarkRun(runRequestBody(sel))
      setEstimate(null)
      setEstimateFor(null)
      onStarted(res)
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(null)
    }
  }

  const updateConfig = (i: number, next: BenchmarkConfig) => setConfigs(configs.map((c, j) => (j === i ? next : c)))
  const addConfig = () => {
    const d = defaultConfig(st, options, configs)
    if (d) setConfigs([...configs, d])
  }
  const canAdd = configs.length < options.max_configs && defaultConfig(st, options, configs) !== null
  const arena = configs.length >= 2
  const startLabel = arena ? `Start arena (${configs.length} engines)` : 'Start run'
  const reasonId = 'bench-start-reason'

  return (
    <BenchSection
      id="run"
      meta={`${caseCount} ${caseCount === 1 ? 'case' : 'cases'} selected${arena ? ' · Model Arena' : ''}`}
      status={running ? runningStatus(job) : null}
      className="bench-run"
    >
      <div className="field-row">
        <Field label="Stage" help="What is being tested: a translation engine, a Whisper model or an OCR backend.">
          <select value={st} onChange={(e) => setStage(e.target.value)}>
            {STAGES.map((s) => (
              <option key={s} value={s}>{STAGE_LABELS[s]}</option>
            ))}
          </select>
        </Field>
        <Field label="Tier">
          <select value={tierValue} onChange={(e) => setTier(e.target.value)}>
            <option value="">Any tier</option>
            {options.tiers.map((t) => (
              <option key={t} value={t}>{TIER_LABELS[t] ?? t}</option>
            ))}
          </select>
        </Field>
        <Field label="Golden set">
          <select value={setValue} onChange={(e) => setSetName(e.target.value)}>
            <option value="">Any set</option>
            {setChoices.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </Field>
      </div>
      {st === 'translation' && caseCount === 0 && <p className="muted" data-testid="bench-no-cases">{NO_CASES_HINT}</p>}
      {st !== 'translation' && caseCount === 0 && (
        <p className="muted">
          {st === 'transcription' ? 'Transcription' : 'OCR'} cases are audio or image files registered on the PC; this page
          can only add translation cases.
        </p>
      )}

      {prefill && <PrefillNote prefill={prefill} onDismiss={() => setPrefill(null)} />}

      <fieldset className="bench-configs">
        <legend>Engines to compare</legend>
        <ol className="bench-config-list">
          {configs.map((c, i) => (
            <li key={i}>
              <ConfigRow stage={st} options={options} config={c} index={i} onChange={(next) => updateConfig(i, next)} />
              {configs.length > 1 && (
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  aria-label={`Remove ${configLabel(st, c.engine, c.model)}`}
                  onClick={() => setConfigs(configs.filter((_, j) => j !== i))}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ol>
        <div className="actions">
          {canAdd && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={addConfig}>
              Add engine
            </button>
          )}
          <span className="muted">
            {arena
              ? 'Model Arena: the same cases go through each engine in turn, then compare them side by side.'
              : `Add up to ${options.max_configs} engines to compare them in a Model Arena.`}
          </span>
        </div>
      </fieldset>

      <div className="field-row">
        <Field label="Run label" help="A name for this run in the list, e.g. “new glossary”.">
          <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} placeholder="Optional" />
        </Field>
        <Field label="Prompt version" help="Your own tag for the prompt or settings being tested, so a change can be proven better or worse. Remembered.">
          <input value={promptVersion} maxLength={60} onChange={(e) => setPromptVersion(e.target.value)} placeholder="e.g. v2" />
        </Field>
      </div>

      {missingKeys.length > 0 && (
        <p className="warn bench-note">
          {missingKeys.map((e) => engineOptionLabel(e)).join(', ')}: no key yet.{' '}
          <ButtonLink href={routeHref({ name: 'settings' })} variant="ghost" size="sm">
            Set a key in Settings
          </ButtonLink>
        </p>
      )}

      {shown && <EstimateBlock est={shown} stage={st} />}

      <div className="actions bench-run-actions">
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={problems.length > 0 || busy !== null}
          onClick={() => void runEstimate()}
        >
          {busy === 'estimate' ? 'Estimating…' : 'Estimate cost'}
        </button>
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={!start.ok || busy !== null}
          aria-describedby={start.ok ? undefined : reasonId}
          onClick={() => void runStart()}
        >
          {busy === 'start' ? 'Starting…' : startLabel}
        </button>
      </div>
      {!start.ok && !running && (
        <p id={reasonId} className="muted" data-testid="start-reason">
          {start.reasons[0]}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {running && <JobProgress job={job} onStop={onStop} />}
    </BenchSection>
  )
}

function ConfigRow({ stage, options, config, index, onChange }: {
  stage: BenchmarkStage
  options: BenchmarkOptions
  config: BenchmarkConfig
  index: number
  onChange: (next: BenchmarkConfig) => void
}) {
  const n = index + 1
  if (stage === 'transcription') {
    return (
      <Field label={`Whisper model ${n}`}>
        <select value={config.model ?? ''} onChange={(e) => onChange({ engine: 'whisper', model: e.target.value })}>
          {options.whisper_sizes.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </Field>
    )
  }
  if (stage === 'ocr') {
    return (
      <Field label={`OCR backend ${n}`}>
        <select value={config.engine} onChange={(e) => onChange({ engine: e.target.value })}>
          {options.ocr_backends.map((b) => (
            <option key={b} value={b}>{OCR_LABELS[b] ?? b}</option>
          ))}
        </select>
      </Field>
    )
  }
  const engine = options.translation_engines.find((e) => e.name === config.engine)
  return (
    <div className="bench-config-fields">
      <Field label={`Engine ${n}`}>
        <select value={config.engine} onChange={(e) => onChange({ engine: e.target.value })}>
          {options.translation_engines.map((e) => (
            <option key={e.name} value={e.name}>{engineOptionLabel(e)}</option>
          ))}
        </select>
      </Field>
      {engine?.models && (
        <Field label={`Model ${n}`}>
          <select
            value={config.model ?? ''}
            onChange={(e) => onChange(e.target.value ? { engine: config.engine, model: e.target.value } : { engine: config.engine })}
          >
            <option value="">Default</option>
            {engine.models.map((m) => (
              <option key={m} value={m}>{modelOptionLabel(engine, m)}</option>
            ))}
          </select>
        </Field>
      )}
    </div>
  )
}

function PrefillNote({ prefill, onDismiss }: { prefill: ComparePrefill; onDismiss: () => void }) {
  const names = prefill.configs.map((c) => configLabel('translation', c.engine, c.model))
  return (
    <div className="bench-prefill" role="status" data-testid="bench-compare-note">
      <p>
        {names.length >= 2
          ? `Set up from Model health: ${names.join(' vs ')}. Pick a golden set, then estimate the cost.`
          : names.length === 1
            ? `Set up from Model health: ${names[0]}. Add another engine to compare, or estimate the cost.`
            : 'Nothing from that Model health link can be run here; your engine picks are unchanged.'}
      </p>
      {prefill.notes.length > 0 && (
        <ul>
          {prefill.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  )
}

export function EstimateBlock({ est, stage }: { est: BenchmarkEstimate; stage: BenchmarkStage }) {
  const capLine = est.monthly_cap_usd > 0
    ? `This month: ${formatCost(est.month_spend_usd)} spent of a ${formatCost(est.monthly_cap_usd)} cap${
        est.remaining_usd != null ? ` · ${formatCost(est.remaining_usd)} left` : ''}`
    : `This month: ${formatCost(est.month_spend_usd)} spent · no monthly cap set`
  const warning = est.monthly_refusal ?? (est.estimate_above_cap
    ? `This run is estimated at ${formatCost(est.estimated_cost_usd)}, more than what's left of this month's cap.`
    : null)
  return (
    <div className="bench-estimate" data-testid="bench-estimate" aria-live="polite">
      <p>
        <strong>Estimate for {est.case_count} {est.case_count === 1 ? 'case' : 'cases'}:</strong>{' '}
        <span className="num">{formatCost(est.estimated_cost_usd)}</span>
        {stage !== 'translation' && <span className="muted"> (runs on this PC; nothing is spent)</span>}
      </p>
      <ul className="bench-estimate-list">
        {est.configs.map((c, i) => (
          <li key={i}>
            <span>{configLabel(stage, c.engine, c.model)}</span>
            <span className="num">{formatCost(c.estimated_cost_usd)}</span>
          </li>
        ))}
      </ul>
      <p className="muted num">{capLine}</p>
      {warning && (
        <p className="warn" role="alert">
          {warning}
        </p>
      )}
    </div>
  )
}

export function JobProgress({ job, onStop }: { job: JobRecord | null; onStop: () => void }) {
  const pct = job?.progress != null ? Math.round(job.progress * 100) : null
  return (
    <div className="bench-progress" data-testid="bench-progress" role="status">
      <div className="bench-progress-line">
        <span>
          Running{pct != null ? ` · ${pct}%` : '…'}
          {job?.message ? <span className="muted"> · {job.message}</span> : null}
        </span>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onStop}>
          Stop
        </button>
      </div>
      <progress max={100} value={pct ?? undefined} aria-label="Benchmark progress" />
    </div>
  )
}
