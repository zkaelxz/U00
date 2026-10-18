import { useCallback, useEffect, useRef, useState } from 'react'

import {
  decideSweep, estimateSweep, getSweep, getSweepDecisions, startSweep,
  type BenchmarkSet, type Sweep, type SweepDecisionRow, type SweepEstimate, type SweepLanguage, type SweepRecommendation,
} from '../../api/benchmark'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { JobRecord } from '../../types/jobs'
import { BenchSection } from './BenchSection'
import { JobProgress } from './RunCard'
import { formatCost, formatScore, formatWhen, plainError, runningStatus } from './benchmarkForm'
import {
  SWEEP_LANGUAGES, canDecide, cerPoints, formatSweepTime, passesLine, recommendationLabel, recommendationTone, startLabel,
  startProblem, stepLabel, sweepKey, sweepSets, sweepStatusLabel, sweepStatusTone,
} from './sweepForm'

type Props = {
  sets: BenchmarkSet[]
  pc: PcMode
  // The page's "benchmark_lab" job: every pass of a sweep is a Lab run under it.
  job: JobRecord | null
  running: boolean
  onStarted: (jobId: string) => void
  onStop: () => void
  onCompare: (runIds: number[]) => void
}

/**
 * "Settings sweep": tunes transcription settings on one golden set and language, one setting at a time, and
 * recommends a change only when it also wins on held-out titles. Nothing is applied until the owner approves.
 */
export function SweepCard({ sets, pc, job, running, onStarted, onStop, onCompare }: Props) {
  const remote = pc === 'remote'
  const names = sweepSets(sets)
  const [storedSet, setSetName] = usePersistedState<string>('benchmark.sweepSet', '')
  const [language, setLanguage] = usePersistedState<SweepLanguage>('benchmark.sweepLanguage', 'zh')
  const setName = names.includes(storedSet) ? storedSet : (names[0] ?? '')
  const key = sweepKey(setName, language)

  const [sweep, setSweep] = useState<Sweep | null>(null)
  const [decisions, setDecisions] = useState<SweepDecisionRow[]>([])
  const [estimate, setEstimate] = useState<SweepEstimate | null>(null)
  const [estimateFor, setEstimateFor] = useState<string | null>(null)
  const [busy, setBusy] = useState<'estimate' | 'start' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [ownRun, setOwnRun] = useState(false)

  const seq = useRef(0)
  const load = useCallback(() => {
    const n = ++seq.current
    getSweep().then((r) => {
      if (n === seq.current) setSweep(r.sweep)
    }, (e: unknown) => setError(plainError(e)))
    getSweepDecisions().then((r) => setDecisions(r.decisions), () => setDecisions([]))
  }, [])
  // On open, and again when the Lab's job ends.
  useEffect(() => {
    if (!running) load()
  }, [running, load])

  const fresh = estimateFor === key
  const shown = fresh ? estimate : null
  const problem = startProblem({ setName, estimate, estimateFresh: fresh, running })
  const sweeping = running && (ownRun || sweep?.status === 'running' || sweep?.status === 'queued')

  const runEstimate = async () => {
    setBusy('estimate')
    setError(null)
    try {
      setEstimate(await estimateSweep({ set_name: setName, language }))
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
      const res = await startSweep({ set_name: setName, language })
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
      id="sweep"
      meta={sweep ? `Latest: ${sweep.set_name} (${sweep.language}) · ${sweepStatusLabel(sweep.status)}` : undefined}
      status={sweeping ? runningStatus(job) : null}
      className="sweep"
    >
      <p className="muted sweep-intro">
        Tries the range of each transcription setting on your own clips, one setting at a time, starting from the
        default. About 30% of the titles are held back to confirm a winner. Nothing changes until you approve it.
      </p>
      {names.length === 0 ? (
        <p className="muted">No golden set has audio clips yet. Upload clips under Golden sets, or add a title&apos;s lines there.</p>
      ) : (
        <div className="sweep-block" role="group" aria-label="Sweep a set">
          <div className="field-row">
            <Field label="Golden set">
              <select value={setName} onChange={(e) => setSetName(e.target.value)}>
                {names.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Language" help="One language per sweep: results are never averaged across languages.">
              <select value={language} onChange={(e) => setLanguage(e.target.value as SweepLanguage)}>
                {SWEEP_LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
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
                aria-describedby={problem ? 'sweep-start-reason' : undefined}
                onClick={() => void start()}
              >
                {busy === 'start' ? 'Starting…' : startLabel(shown)}
              </button>
            )}
            {!remote && problem && (
              <span className="muted" id="sweep-start-reason" data-testid="sweep-start-reason">
                {problem}
              </span>
            )}
          </div>
          <div role="status">{shown && <SweepEstimateBlock est={shown} />}</div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
      {sweeping && <JobProgress job={job} onStop={onStop} />}
      {sweep && <LatestSweep sweep={sweep} remote={remote} onCompare={onCompare} onDecided={(s) => {
        setSweep(s)
        load()
      }} />}
      {decisions.length > 0 && <DecisionHistory decisions={decisions} />}
    </BenchSection>
  )
}

function SweepEstimateBlock({ est }: { est: SweepEstimate }) {
  const left = [...est.models.filter((m) => !m.included).map((m) => ({ name: m.label, reason: m.reason })),
    ...est.knobs.filter((k) => !k.included).map((k) => ({ name: k.label, reason: k.reason }))]
  return (
    <div className="bench-estimate" data-testid="sweep-estimate">
      <p>
        <strong>{est.passes_to_run} {est.passes_to_run === 1 ? 'pass' : 'passes'} to run, {formatSweepTime(est.estimated_seconds)}</strong>
        <span className="muted"> ({est.use_gpu ? 'GPU' : 'CPU'}, runs on this PC)</span>
      </p>
      <p className="muted" data-testid="sweep-cost">
        Cost {formatCost(est.estimated_cost_usd)}: transcription runs on this PC, so{' '}
        {est.monthly_cap_usd > 0 ? `the ${formatCost(est.monthly_cap_usd)} monthly cap for paid engines` : 'the monthly cap for paid engines'} doesn&apos;t apply.
      </p>
      {est.passes_done > 0 && (
        <p className="muted">{est.passes_done} {est.passes_done === 1 ? 'pass has' : 'passes have'} already run on these clips and will be skipped.</p>
      )}
      <ul className="bench-estimate-list">
        <li>
          <span>Tuning: {est.tuning_titles} {est.tuning_titles === 1 ? 'title' : 'titles'}</span>
          <span className="num">{est.tuning_cases} clips</span>
        </li>
        <li>
          <span>Held out to confirm: {est.holdout_titles} {est.holdout_titles === 1 ? 'title' : 'titles'}</span>
          <span className="num">{est.holdout_cases} clips</span>
        </li>
        <li>
          <span>Audio</span>
          <span className="num">{Math.round(est.audio_seconds / 60)} min{est.durations_guessed ? ' (some lengths guessed)' : ''}</span>
        </li>
      </ul>
      <p>
        <span className="muted">Models:</span> {est.models.filter((m) => m.included).map((m) => m.label).join(', ')}
      </p>
      <p>
        <span className="muted">Settings:</span> {est.knobs.filter((k) => k.included).map((k) => `${k.label} (${k.values.map((v) => (typeof v === 'boolean' ? (v ? 'on' : 'off') : v)).join(', ')})`).join('; ')}
      </p>
      {est.small_set && (
        <p className="warn" role="note">
          Only {est.tuning_cases + est.holdout_cases} clips. A result counts from about {est.min_set_cases} clips; this one is a first look.
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

function LatestSweep({ sweep, remote, onCompare, onDecided }: {
  sweep: Sweep
  remote: boolean
  onCompare: (runIds: number[]) => void
  onDecided: (s: Sweep | null) => void
}) {
  const rec = sweep.recommendation
  return (
    <div className="sweep-block" role="group" aria-labelledby="sweep-latest-h" data-testid="sweep-latest">
      <h4 id="sweep-latest-h">Latest sweep</h4>
      <p className="sweep-head">
        <strong>{sweep.set_name}</strong> <span className="muted">({sweep.language})</span>{' '}
        <Badge tone={sweepStatusTone(sweep.status)}>{sweepStatusLabel(sweep.status)}</Badge>{' '}
        <span className="muted num">{formatWhen(sweep.started_at)} · {passesLine(sweep)}</span>
      </p>
      {sweep.note && <p className="muted">{sweep.note}</p>}
      {rec && <Recommendation rec={rec} onCompare={onCompare} />}
      {sweep.decision ? (
        <p role="status" data-testid="sweep-decision">
          {sweep.decision.decision === 'promoted'
            ? `Approved ${formatWhen(sweep.decision.decided_at)}. Saved as the profile “${sweep.decision.profile?.name ?? ''}”: pick it at New title, or make it your default profile.`
            : `Rejected ${formatWhen(sweep.decision.decided_at)}. The default stays.`}
          {sweep.decision.reason ? ` Reason: ${sweep.decision.reason}` : ''}
        </p>
      ) : (
        <DecideBlock sweep={sweep} remote={remote} onDecided={onDecided} />
      )}
    </div>
  )
}

function Recommendation({ rec, onCompare }: { rec: SweepRecommendation; onCompare: (runIds: number[]) => void }) {
  const h = rec.holdout
  // A five-column table squeezes the tag names on a phone; a list keeps each tag on its own lines.
  const phone = useMediaQuery('(max-width: 640px)')
  return (
    <div className="sweep-rec" data-testid="sweep-recommendation">
      <p>
        <Badge tone={recommendationTone(rec.state)}>{recommendationLabel(rec.state)}</Badge> {rec.text}
      </p>
      <ul className="bench-estimate-list">
        <li>
          <span>Default</span>
          <span>{rec.default.label}</span>
        </li>
        <li>
          <span>Best on the tuning titles</span>
          <span>{rec.winner.label}</span>
        </li>
        <li>
          <span>Tuning score, default → best</span>
          <span className="num">{formatScore(rec.tuning.default_score)} → {formatScore(rec.tuning.winner_score)}</span>
        </li>
        {h && (
          <li>
            <span>Held-out score, default → best</span>
            <span className="num">{formatScore(h.default_score)} → {formatScore(h.winner_score)} ({cerPoints(h.delta)})</span>
          </li>
        )}
      </ul>
      <p className="muted">{rec.margin_rule}</p>
      {rec.small_set && (
        <p className="warn" role="note">
          Only {rec.case_counts.tuning + rec.case_counts.holdout} clips: treat this as a first look.
        </p>
      )}
      {h && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onCompare([h.default_run_id, h.winner_run_id])}>
            Compare the hold-out runs
          </button>
        </div>
      )}
      {rec.tags.length > 0 && phone && (
        <ul className="sweep-tags" aria-label="Held-out score by tag">
          {rec.tags.map((t) => (
            <li key={t.tag}>
              <strong>{t.tag}</strong> <span className="muted num">{t.cases} clips</span>
              <span className="num">{formatScore(t.default_score)} → {formatScore(t.winner_score)}</span>
              <span>{cerPoints(t.delta)} {t.too_small && <Badge tone="neutral">Too few clips to decide</Badge>}</span>
            </li>
          ))}
        </ul>
      )}
      {rec.tags.length > 0 && !phone && (
        <div className="table-scroll">
          <table className="bench-table" aria-label="Held-out score by tag">
            <thead>
              <tr>
                <th>Tag</th>
                <th className="num">Clips</th>
                <th className="num">Default</th>
                <th className="num">Best</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {rec.tags.map((t) => (
                <tr key={t.tag}>
                  <td>{t.tag}</td>
                  <td className="num">{t.cases}</td>
                  <td className="num">{formatScore(t.default_score)}</td>
                  <td className="num">{formatScore(t.winner_score)}</td>
                  <td>
                    {cerPoints(t.delta)} {t.too_small && <Badge tone="neutral">Too few clips to decide</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details>
        <summary>Every pass ({rec.steps.length})</summary>
        <ol className="sweep-steps">
          {rec.steps.map((s, i) => (
            <li key={i}>
              <span>{stepLabel(s)}</span>{' '}
              <span className="muted">{s.half === 'holdout' ? 'held-out titles' : 'tuning titles'} · {s.setup}</span>{' '}
              <span className="num">{formatScore(s.score)}</span> {s.kept && <Badge tone="ok">Kept</Badge>}
            </li>
          ))}
        </ol>
      </details>
    </div>
  )
}

function DecideBlock({ sweep, remote, onDecided }: { sweep: Sweep; remote: boolean; onDecided: (s: Sweep | null) => void }) {
  const can = canDecide(sweep)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!can.reject || remote) return null
  const decide = async (decision: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      onDecided((await decideSweep(decision, reason.trim())).sweep)
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="sweep-decide">
      {can.approve && sweep.applies && sweep.applies.length > 0 && (
        <div data-testid="sweep-applies">
          <p>
            Approving saves a setting profile and changes no title. A title gets these settings only when you apply the profile to it:
          </p>
          <ul className="bench-estimate-list" aria-label="What applying the profile sets on a title">
            {sweep.applies.map((a) => (
              <li key={a.key}>
                <span>{a.label}</span>
                <span className="num">{a.value}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Field label="Why?" help="Kept with the decision.">
        <input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} placeholder="Optional" />
      </Field>
      <div className="actions">
        {can.approve && (
          <ConfirmButton
            size="sm"
            name="the winner"
            label="Approve…"
            verb="approve"
            tone="primary"
            confirmLabel="Save as a setting profile"
            busy={busy}
            onConfirm={() => void decide('approve')}
          />
        )}
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => void decide('reject')}>
          Reject, keep the default
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function DecisionHistory({ decisions }: { decisions: SweepDecisionRow[] }) {
  return (
    <details className="sweep-history">
      <summary>Earlier decisions ({decisions.length})</summary>
      <ul>
        {decisions.map((d, i) => (
          <li key={i}>
            <span className="num">{formatWhen(d.decided_at)}</span> {d.decision === 'promoted' ? 'Approved' : 'Rejected'}{' '}
            {d.setup} <span className="muted">({d.language})</span>
            {d.reason ? <span className="muted"> · {d.reason}</span> : null}
          </li>
        ))}
      </ul>
    </details>
  )
}
