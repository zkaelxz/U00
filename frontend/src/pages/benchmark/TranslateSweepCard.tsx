import { useCallback, useEffect, useRef, useState } from 'react'

import {
  decideTranslateSweep, estimateTranslateSweep, getTranslateSweep, getTranslateSweepDecisions, startTranslateSweep,
  type BenchmarkOptions, type BenchmarkSet, type SweepDecisionRow, type TranslateSweep, type TranslateSweepEstimate,
} from '../../api/benchmark'
import { modelOptionLabel } from '../../api/translate'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { JobRecord } from '../../types/jobs'
import { engineOptionLabel } from '../translatePage'
import { BenchSection } from './BenchSection'
import { JobProgress } from './RunCard'
import { DecisionHistory, LatestSweep, type SweepWording } from './SweepCard'
import { plainError, runningStatus } from './benchmarkForm'
import { SWEEP_LANGUAGES, sweepStatusLabel } from './sweepForm'
import {
  chrfPoints, costLine, translateStartLabel, translateStartProblem, translateStepLabel, translateSweepKey, translateSweepSets,
} from './translateSweepForm'

type Props = {
  options: BenchmarkOptions
  sets: BenchmarkSet[]
  pc: PcMode
  // The page's "benchmark_lab" job: every pass of a sweep is a Lab run under it.
  job: JobRecord | null
  running: boolean
  onStarted: (jobId: string) => void
  onStop: () => void
  onCompare: (runIds: number[]) => void
}

const WORDING: SweepWording = { noun: 'cases', points: chrfPoints, step: translateStepLabel, decide: decideTranslateSweep }

/**
 * "Translation sweep": tunes a translation engine's batch size, context, Reflect and style on one golden set and
 * language, one setting at a time, and recommends a change only when it also wins on held-out titles. Paid engines
 * are held to the monthly cap. Nothing is applied until the owner approves.
 */
export function TranslateSweepCard({ options, sets, pc, job, running, onStarted, onStop, onCompare }: Props) {
  const remote = pc === 'remote'
  const names = translateSweepSets(sets)
  const [storedSet, setSetName] = usePersistedState<string>('benchmark.tsweepSet', '')
  const [language, setLanguage] = usePersistedState<'zh' | 'ja' | 'ko'>('benchmark.tsweepLanguage', 'zh')
  const [storedEngine, setEngine] = usePersistedState<string>('benchmark.tsweepEngine', '')
  const [model, setModel] = usePersistedState<string>('benchmark.tsweepModel', '')
  const [seriesText, setSeries] = usePersistedState<string>('benchmark.tsweepSeries', '')
  const setName = names.includes(storedSet) ? storedSet : (names[0] ?? '')
  const engines = options.translation_engines
  const engine = engines.find((e) => e.name === storedEngine) ?? engines.find((e) => e.key_configured) ?? engines[0]
  const modelValue = engine?.models?.includes(model) ? model : ''
  const choices = options.translate_choices
  const seriesId = choices.series.some((s) => String(s.id) === seriesText) ? Number(seriesText) : null
  const key = translateSweepKey(setName, language, engine?.name ?? '', modelValue, seriesId)

  const [sweep, setSweep] = useState<TranslateSweep | null>(null)
  const [decisions, setDecisions] = useState<SweepDecisionRow[]>([])
  const [estimate, setEstimate] = useState<TranslateSweepEstimate | null>(null)
  const [estimateFor, setEstimateFor] = useState<string | null>(null)
  const [busy, setBusy] = useState<'estimate' | 'start' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ownRun, setOwnRun] = useState(false)

  const seq = useRef(0)
  const load = useCallback(() => {
    const n = ++seq.current
    getTranslateSweep().then((r) => {
      if (n === seq.current) setSweep(r.sweep)
    }, (e: unknown) => setError(plainError(e)))
    getTranslateSweepDecisions().then((r) => setDecisions(r.decisions), () => setDecisions([]))
  }, [])
  // On open, and again when the Lab's job ends.
  useEffect(() => {
    if (!running) load()
  }, [running, load])

  const fresh = estimateFor === key
  const shown = fresh ? estimate : null
  const problem = translateStartProblem({ setName, engine: engine?.name ?? '', estimate, estimateFresh: fresh, running })
  const sweeping = running && (ownRun || sweep?.status === 'running' || sweep?.status === 'queued')
  const request = { set_name: setName, language, engine: engine?.name ?? '', model: modelValue || null, series_id: seriesId }

  const runEstimate = async () => {
    setBusy('estimate')
    setError(null)
    try {
      setEstimate(await estimateTranslateSweep(request))
      setEstimateFor(key)
    } catch (e) {
      setError(plainError(e))
    } finally {
      setBusy(null)
    }
  }
  const start = async () => {
    setBusy('start')
    setError(null)
    try {
      const res = await startTranslateSweep(request)
      setOwnRun(true)
      setSweep(res.sweep)
      setEstimateFor(null)
      onStarted(res.job_id)
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <BenchSection
      id="tsweep"
      meta={sweep ? `Latest: ${sweep.set_name} (${sweep.language}) · ${sweepStatusLabel(sweep.status)}` : undefined}
      status={sweeping ? runningStatus(job) : null}
      className="sweep"
    >
      <p className="muted sweep-intro">
        Tries the batch size, context lines, Reflect and style of one translation engine on your own cases, one setting at a
        time, starting from what a title uses. About 30% of the titles are held back to confirm a winner. Nothing changes
        until you approve it.
      </p>
      {names.length === 0 || !engine ? (
        <p className="muted">No golden set has translation cases with a reference yet. Upload clips with a translation under Golden sets.</p>
      ) : (
        <div className="sweep-block" role="group" aria-label="Sweep a translation engine">
          <div className="field-row">
            <Field label="Golden set">
              <select value={setName} onChange={(e) => setSetName(e.target.value)}>
                {names.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Language" help="One language per sweep: results are never averaged across languages.">
              <select value={language} onChange={(e) => setLanguage(e.target.value as 'zh' | 'ja' | 'ko')}>
                {SWEEP_LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="field-row">
            <Field label="Engine" help="The engine whose settings are tuned. A paid engine is estimated first and held to the monthly cap.">
              <select value={engine.name} onChange={(e) => { setEngine(e.target.value); setModel('') }}>
                {engines.map((e) => (
                  <option key={e.name} value={e.name}>{engineOptionLabel(e)}</option>
                ))}
              </select>
            </Field>
            {engine.models && (
              <Field label="Model" help="The model to start from. The sweep also tries the engine's other models.">
                <select value={modelValue} onChange={(e) => setModel(e.target.value)}>
                  <option value="">Default</option>
                  {engine.models.map((m) => (
                    <option key={m} value={m}>{modelOptionLabel(engine, m)}</option>
                  ))}
                </select>
              </Field>
            )}
            <Field label="Series" help="Translates every pass with this series' glossary, learned style and character hints. None: without them.">
              <select value={seriesId ?? ''} onChange={(e) => setSeries(e.target.value)}>
                <option value="">None</option>
                {choices.series.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy !== null || !setName} onClick={() => void runEstimate()}>
              {busy === 'estimate' ? 'Estimating…' : 'Estimate'}
            </button>
            {!remote && (
              <button
                type="button"
                className={buttonClass('primary')}
                disabled={busy !== null || !!problem}
                aria-describedby={problem ? 'tsweep-start-reason' : undefined}
                onClick={() => void start()}
              >
                {busy === 'start' ? 'Starting…' : translateStartLabel(shown)}
              </button>
            )}
            {!remote && problem && (
              <span className="muted" id="tsweep-start-reason" data-testid="tsweep-start-reason">
                {problem}
              </span>
            )}
          </div>
          <div role="status">{shown && <TranslateSweepEstimateBlock est={shown} />}</div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
      {sweeping && <JobProgress job={job} onStop={onStop} />}
      {sweep && (
        <LatestSweep
          sweep={sweep}
          remote={remote}
          onCompare={onCompare}
          wording={WORDING}
          detail={<span className="muted"> · {sweep.engine}{sweep.model ? ` ${sweep.model}` : ''}</span>}
          onDecided={(s) => {
            setSweep(s as TranslateSweep | null)
            load()
          }}
        />
      )}
      {decisions.length > 0 && <DecisionHistory decisions={decisions} />}
    </BenchSection>
  )
}

function TranslateSweepEstimateBlock({ est }: { est: TranslateSweepEstimate }) {
  const left = [...est.models.filter((m) => !m.included).map((m) => ({ name: m.label, reason: m.reason })),
    ...est.knobs.filter((k) => !k.included).map((k) => ({ name: k.label, reason: k.reason }))]
  const shownValue = (v: boolean | number | string | null) => (v === null ? 'default' : typeof v === 'boolean' ? (v ? 'on' : 'off') : String(v).replace(/_/g, ' '))
  return (
    <div className="bench-estimate" data-testid="tsweep-estimate">
      <p>
        <strong>{est.passes_to_run} {est.passes_to_run === 1 ? 'pass' : 'passes'} to run</strong>
        <span className="muted"> ({est.engine}{est.model ? ` ${est.model}` : ''})</span>
      </p>
      <p className="muted" data-testid="tsweep-cost">{costLine(est)}</p>
      {est.passes_done > 0 && (
        <p className="muted">{est.passes_done} {est.passes_done === 1 ? 'pass has' : 'passes have'} already run on these cases and will be skipped.</p>
      )}
      <ul className="bench-estimate-list">
        <li>
          <span>Tuning: {est.tuning_titles} {est.tuning_titles === 1 ? 'title' : 'titles'}</span>
          <span className="num">{est.tuning_cases} cases</span>
        </li>
        <li>
          <span>Held out to confirm: {est.holdout_titles} {est.holdout_titles === 1 ? 'title' : 'titles'}</span>
          <span className="num">{est.holdout_cases} cases</span>
        </li>
      </ul>
      <p>
        <span className="muted">Models:</span> {[est.model ?? 'default', ...est.models.filter((m) => m.included).map((m) => m.label)].join(', ')}
      </p>
      <p>
        <span className="muted">Settings:</span> {est.knobs.filter((k) => k.included).map((k) => `${k.label} (${k.values.map(shownValue).join(', ')})`).join('; ')}
      </p>
      {est.small_set && (
        <p className="warn" role="note">
          Only {est.tuning_cases + est.holdout_cases} cases. A result counts from about {est.min_set_cases} cases; this one is a first look.
        </p>
      )}
      {left.length > 0 && (
        <ul className="sweep-left-out" aria-label="Left out of this sweep">
          {left.map((l) => (
            <li key={l.name}>
              <strong>{l.name}</strong> <span className="muted">left out: {l.reason}</span>
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary>Not swept</summary>
        <ul>
          {est.not_swept.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}
