import { useCallback, useEffect, useState } from 'react'

import type { BenchmarkEstimate, BenchmarkOptions, BenchmarkSet } from '../../api/benchmark'
import { modelOptionLabel } from '../../api/translate'
import {
  addReevalCandidate, estimateReeval, getReevalDecisions, getReevalOverview, promoteReevalCandidate,
  rejectReevalCandidate, reopenReevalCandidate, saveReevalSettings, startReevalRun,
  type ModelCandidate, type ModelDecision, type ReevalOverview, type ReevalRow, type ReevalRunStarted,
  type ReevalSettingsSaved,
} from '../../api/reeval'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { safeDetail } from '../../components/errorMessages'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, type PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { routeHref } from '../../router'
import type { JobRecord } from '../../types/jobs'
import { engineOptionLabel } from '../translatePage'
import { EstimateBlock, JobProgress } from './RunCard'
import {
  TIER_LABELS, deltaTone, formatCost, formatDelta, formatLatency, formatScore, formatWhen, plainError, runStatusLabel,
  runStatusTone, setOptions, tierLabel,
} from './benchmarkForm'
import {
  MAX_NOTE_CHARS, MAX_REASON_CHARS, addOutcome, canPromote, canReopen, candidateBody, candidateEngines, candidateStatusLabel,
  candidateStatusTone, decisionLabel, decisionScores, draftFromSettings, formatCostDelta, formatLatencyDelta,
  formatVramDelta, intervalProblem, limitProblem, lowerIsBetterTone, modelLabel, nextDueText, openCandidates, productionSource,
  promoteEffects, reevalEstimateKey, reevalMissingKeys, reportActive, rowFor, runCandidates, runNowState, sameModel,
  scheduleBody, scheduleDirty,
  scheduleSummary, setChoiceDirty, type AddOutcome, type ScheduleDraft,
} from './reeval'

type Props = {
  options: BenchmarkOptions
  sets: BenchmarkSet[]
  pc: PcMode
  phone: boolean
  // The page's "benchmark_lab" job (a re-evaluation is a benchmark run).
  job: JobRecord | null
  running: boolean
  onStarted: (res: ReevalRunStarted) => void
  onStop: () => void
  // Opens the Arena for [production run, candidate run].
  onCompare: (runIds: number[]) => void
}

type Msg = { tone: 'ok' | 'known' | 'error'; text: string }

/**
 * "Model re-evaluation" (Step 40b): is a newer model better than the one in
 * production? Candidates run against production through the Benchmark Lab,
 * on "Run now" or an opt-in schedule; nothing is promoted without the
 * explicit second press. Writes are PC only.
 */
export function ReevalCard({ options, sets, pc, phone, job, running, onStarted, onStop, onCompare }: Props) {
  const [overview, setOverview] = useState<ReevalOverview | null>(null)
  const [decisions, setDecisions] = useState<ModelDecision[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ownRun, setOwnRun] = useState(false)
  // What one scheduled run would cost, from the last save of an enabled schedule.
  const [scheduleEstimate, setScheduleEstimate] = useState<ScheduleEstimate | null>(null)

  const load = useCallback(() => {
    getReevalOverview().then(
      (o) => {
        setOverview(o)
        setLoadError(null)
      },
      (e: unknown) => setLoadError(plainError(e)),
    )
  }, [])
  const loadDecisions = useCallback(() => {
    getReevalDecisions().then((r) => setDecisions(r.decisions), () => setDecisions(null))
  }, [])
  // On open, and again when a run finishes.
  useEffect(() => {
    if (running) return
    load()
    loadDecisions()
  }, [running, load, loadDecisions])

  const remote = pc === 'remote'
  const changed = () => {
    // Candidates changed: the last schedule estimate no longer describes a run.
    setScheduleEstimate(null)
    load()
    loadDecisions()
  }

  if (!overview) {
    return (
      <Card title="Model re-evaluation" className="reeval" aria-label="Model re-evaluation">
        {loadError ? (
          <p className="error" role="alert">
            {loadError}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </Card>
    )
  }

  const open = openCandidates(overview.candidates)
  const showProgress = running && (ownRun || reportActive(overview))

  return (
    <Card
      title="Model re-evaluation"
      meta={`${open.length} open ${open.length === 1 ? 'candidate' : 'candidates'} · schedule ${overview.settings.schedule_enabled ? 'on' : 'off'}`}
      className="reeval"
      aria-label="Model re-evaluation"
    >
      <p className="muted reeval-intro">
        Is a newer model better than the one in production? Add candidate models, run them against production on a
        golden set, and promote one only if you decide to. Nothing changes by itself.
      </p>
      <p className="reeval-production" data-testid="reeval-production">
        <span className="muted">Production model:</span> <strong>{modelLabel(overview.production)}</strong>{' '}
        <Badge tone={overview.production.source === 'promoted' ? 'accent' : 'neutral'}>{productionSource(overview)}</Badge>
      </p>

      <CandidatesBlock overview={overview} options={options} remote={remote} onChanged={changed} />

      <RunBlock
        overview={overview}
        options={options}
        remote={remote}
        running={running}
        onStarted={(res) => {
          setOwnRun(true)
          onStarted(res)
          load()
        }}
      />
      {showProgress && <JobProgress job={job} onStop={onStop} />}

      <ReportBlock overview={overview} phone={phone} remote={remote} onCompare={onCompare} onChanged={changed} />

      <ScheduleSection
        key={JSON.stringify(overview.settings)}
        overview={overview}
        options={options}
        sets={sets}
        remote={remote}
        savedEstimate={scheduleEstimate}
        onSaved={(o) => {
          setOverview(o)
          setScheduleEstimate(o.settings.schedule_enabled
            ? { est: o.schedule_estimate ?? null, error: o.schedule_estimate_error ?? null }
            : null)
        }}
      />
      <HistorySection decisions={decisions} candidates={overview.candidates} />
    </Card>
  )
}

function MsgLine({ msg, testId }: { msg: Msg | null; testId?: string }) {
  if (!msg) return null
  if (msg.tone === 'error') {
    return (
      <p className="error" role="alert">
        {msg.text}
      </p>
    )
  }
  return (
    <p className={msg.tone === 'known' ? 'reeval-known' : undefined} role="status" data-testid={testId}>
      {msg.text}
    </p>
  )
}

// ---- candidates ----

function CandidatesBlock({ overview, options, remote, onChanged }: {
  overview: ReevalOverview
  options: BenchmarkOptions
  remote: boolean
  onChanged: () => void
}) {
  const [rejecting, setRejecting] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [msg, setMsg] = useState<Msg | null>(null)
  const list = overview.candidates

  const act = async (id: number, fn: () => Promise<{ candidate: ModelCandidate }>, done: (c: ModelCandidate) => string) => {
    setBusyId(id)
    setMsg(null)
    try {
      const r = await fn()
      setMsg({ tone: 'ok', text: done(r.candidate) })
      setRejecting(null)
      setReason('')
      onChanged()
    } catch (e) {
      setMsg({ tone: 'error', text: plainError(e, { pcOnly: true }) })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="reeval-block" role="group" aria-labelledby="reeval-candidates-h">
      <h4 id="reeval-candidates-h">Candidates</h4>
      {list.length === 0 ? (
        <p className="muted">No candidates yet. Add a model below to compare it with production.</p>
      ) : (
        <ul className="reeval-list" aria-label="Candidate models">
          {list.map((c) => (
            <li key={c.id}>
              <div className="reeval-item-head">
                <strong>{modelLabel(c)}</strong>
                <Badge tone={candidateStatusTone(c.status)}>{candidateStatusLabel(c.status)}</Badge>
                <span className="muted num">added {formatWhen(c.created_at)}</span>
              </div>
              {c.note && <p className="muted">{c.note}</p>}
              {c.status !== 'candidate' && c.last_decision && <p className="reeval-summary">{c.last_decision.summary}</p>}
              {c.status === 'superseded' && <p className="muted">A later promotion replaced it as production.</p>}
              {c.status === 'candidate' && sameModel(c, overview.production) && (
                <p className="muted">This is the production model now (Settings changed), so runs leave it out.</p>
              )}
              {!remote && c.status === 'candidate' && rejecting !== c.id && (
                <div className="actions">
                  <button
                    type="button"
                    className={buttonClass('ghost', 'sm')}
                    aria-label={`Reject ${modelLabel(c)}`}
                    disabled={busyId !== null}
                    onClick={() => {
                      setRejecting(c.id)
                      setReason('')
                    }}
                  >
                    Reject…
                  </button>
                </div>
              )}
              {!remote && rejecting === c.id && (
                <div className="reeval-reject">
                  <Field label="Why reject it?" help="Kept with the decision, so re-adding this model later shows it instead of starting over.">
                    <input value={reason} maxLength={MAX_REASON_CHARS} onChange={(e) => setReason(e.target.value)} placeholder="Optional" />
                  </Field>
                  <div className="actions">
                    <button
                      type="button"
                      className={buttonClass('danger', 'sm')}
                      disabled={busyId !== null}
                      onClick={() => void act(c.id, () => rejectReevalCandidate(c.id, reason.trim()), (x) => `Rejected ${modelLabel(x)}. It is left out of later runs.`)}
                    >
                      {busyId === c.id ? 'Rejecting…' : `Reject ${modelLabel(c)}`}
                    </button>
                    <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setRejecting(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              {!remote && canReopen(c.status) && (
                <div className="actions">
                  <button
                    type="button"
                    className={buttonClass('secondary', 'sm')}
                    aria-label={`Reopen ${modelLabel(c)}`}
                    disabled={busyId !== null}
                    onClick={() => void act(c.id, () => reopenReevalCandidate(c.id), (x) => `${modelLabel(x)} is back in the running.`)}
                  >
                    {busyId === c.id ? 'Reopening…' : 'Reopen'}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {remote ? (
        <p className="muted">Adding, rejecting and promoting candidates is PC only.</p>
      ) : (
        <AddCandidate options={options} openCount={openCandidates(list).length} onAdded={onChanged} />
      )}
      <MsgLine msg={msg} testId="reeval-candidate-status" />
    </div>
  )
}

function AddCandidate({ options, openCount, onAdded }: { options: BenchmarkOptions; openCount: number; onAdded: () => void }) {
  const engines = candidateEngines(options.translation_engines)
  const [engineName, setEngineName] = usePersistedState<string>('benchmark.reevalEngine', '')
  const engine = engines.find((e) => e.name === engineName) ?? engines[0]
  const [model, setModel] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<AddOutcome | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!engine) return null
  const modelValue = engine.models?.includes(model) ? model : ''

  const submit = async () => {
    setBusy(true)
    setError(null)
    setOutcome(null)
    try {
      const res = await addReevalCandidate(candidateBody({ engine: engine.name, model: modelValue, note }))
      setOutcome(addOutcome(res))
      if (!res.already_registered) setNote('')
      onAdded()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="reeval-add" role="group" aria-label="Add a candidate">
      <div className="field-row">
        <Field label="Candidate engine">
          <select
            value={engine.name}
            onChange={(e) => {
              setEngineName(e.target.value)
              setModel('')
            }}
          >
            {engines.map((e) => (
              <option key={e.name} value={e.name}>{engineOptionLabel(e)}</option>
            ))}
          </select>
        </Field>
        {engine.models && (
          <Field label="Candidate model">
            <select value={modelValue} onChange={(e) => setModel(e.target.value)}>
              <option value="">Default</option>
              {engine.models.map((m) => (
                <option key={m} value={m}>{modelOptionLabel(engine, m)}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Note" help="Why you want to try it, e.g. “new release, cheaper”.">
          <input value={note} maxLength={MAX_NOTE_CHARS} onChange={(e) => setNote(e.target.value)} placeholder="Optional" />
        </Field>
      </div>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void submit()}>
          {busy ? 'Adding…' : 'Add candidate'}
        </button>
        {openCount >= 3 && <span className="muted">Up to 3 open candidates; promote or reject one to add another.</span>}
      </div>
      {outcome?.kind === 'known' && (
        <p className="reeval-known" role="status" data-testid="reeval-known">
          {outcome.text}
        </p>
      )}
      {outcome?.kind === 'added' && (
        <p role="status" data-testid="reeval-added">
          {outcome.text}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- estimate and run ----

function RunBlock({ overview, options, remote, running, onStarted }: {
  overview: ReevalOverview
  options: BenchmarkOptions
  remote: boolean
  running: boolean
  onStarted: (res: ReevalRunStarted) => void
}) {
  const [estimate, setEstimate] = useState<BenchmarkEstimate | null>(null)
  const [estimateFor, setEstimateFor] = useState<string | null>(null)
  const [busy, setBusy] = useState<'estimate' | 'run' | null>(null)
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null)
  const key = reevalEstimateKey(overview)
  const shown = estimate && estimateFor === key ? estimate : null
  const missingKeys = reevalMissingKeys(overview, options.translation_engines)
  const state = runNowState({ overview, estimate, estimateFor, pcRemote: remote, running, missingKeys })
  const noCandidates = runCandidates(overview).length === 0
  const error = failure && failure.key === key ? failure.text : null
  const reasonId = 'reeval-run-reason'
  const s = overview.settings

  const runEstimate = async () => {
    setBusy('estimate')
    setFailure(null)
    try {
      const est = await estimateReeval()
      setEstimate(est)
      setEstimateFor(key)
    } catch (e) {
      setFailure({ key, text: plainError(e) })
    } finally {
      setBusy(null)
    }
  }
  const runNow = async () => {
    setBusy('run')
    setFailure(null)
    try {
      const res = await startReevalRun()
      setEstimate(null)
      setEstimateFor(null)
      onStarted(res)
    } catch (e) {
      setFailure({ key, text: plainError(e, { pcOnly: true }) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="reeval-block" role="group" aria-labelledby="reeval-run-h">
      <h4 id="reeval-run-h">Run now</h4>
      <p className="muted">
        Runs production and every open candidate on {s.set_name ? `the “${s.set_name}” set` : s.tier ? `${tierLabel(s.tier).toLowerCase()} sets` : 'every translation case'}
        {' '}(change it under Schedule and golden set). It spends like any benchmark run; the monthly cap applies if one is set in Settings.
      </p>
      {missingKeys.length > 0 && (
        <p className="warn bench-note">
          {missingKeys.map((e) => engineOptionLabel(e)).join(', ')}: no key yet.{' '}
          <ButtonLink href={routeHref({ name: 'settings' })} variant="ghost" size="sm">
            Set a key in Settings
          </ButtonLink>
        </p>
      )}
      {shown && <EstimateBlock est={shown} stage="translation" />}
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={noCandidates || busy !== null} onClick={() => void runEstimate()}>
          {busy === 'estimate' ? 'Estimating…' : 'Estimate cost'}
        </button>
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={!state.ok || busy !== null}
          aria-describedby={state.ok ? undefined : reasonId}
          onClick={() => void runNow()}
        >
          {busy === 'run' ? 'Starting…' : 'Run now'}
        </button>
      </div>
      {!state.ok && !running && (
        <p id={reasonId} className="muted" data-testid="reeval-run-reason">
          {state.reasons[0]}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

// ---- report and promotion ----

function ReportBlock({ overview, phone, remote, onCompare, onChanged }: {
  overview: ReevalOverview
  phone: boolean
  remote: boolean
  onCompare: (runIds: number[]) => void
  onChanged: () => void
}) {
  const { report } = overview
  const [msg, setMsg] = useState<Msg | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const refused = report.error ? safeDetail(report.error) ?? 'The last scheduled run could not start.' : null
  const prod = report.production_run

  const promote = async (row: ReevalRow, reason: string) => {
    setBusyId(row.candidate.id)
    setMsg(null)
    try {
      const r = await promoteReevalCandidate(row.candidate.id, reason.trim())
      const engineNote = r.default_engine_changed ? ` Settings' default engine is now ${humanize('engine', r.production.engine)}.` : ''
      setMsg({ tone: 'ok', text: `${modelLabel(r.production)} is now the production model.${engineNote}` })
      onChanged()
    } catch (e) {
      setMsg({ tone: 'error', text: plainError(e, { pcOnly: true }) })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="reeval-block" role="group" aria-labelledby="reeval-report-h">
      <h4 id="reeval-report-h">Latest report</h4>
      {refused && (
        <p className="warn" role="alert" data-testid="reeval-report-error">
          Last scheduled attempt{report.error_at ? ` ${formatWhen(report.error_at)} (UTC)` : ''}: {refused}
        </p>
      )}
      {report.rows.length === 0 ? (
        <p className="muted">
          {report.started_at ? 'The last run has no candidate results to show.' : 'No re-evaluation yet. Estimate the cost, then press Run now.'}
        </p>
      ) : (
        <>
          <p className="muted num" data-testid="reeval-baseline">
            {report.scheduled ? 'Scheduled run' : 'Run'} of {formatWhen(report.started_at)} (UTC). Production {modelLabel(report.production)}
            {prod ? `: score ${formatScore(prod.aggregate_score)} · ${formatCost(prod.total_cost_usd)} · ${formatLatency(prod.avg_latency_seconds)} avg` : ''}.
            Differences are candidate minus production.
          </p>
          <ul className={phone ? 'reeval-rows reeval-rows-phone' : 'reeval-rows'} aria-label="Candidates against production">
            {report.rows.map((row) => (
              <ReportRow
                key={row.candidate.id}
                row={row}
                overview={overview}
                remote={remote}
                busy={busyId === row.candidate.id}
                blocked={busyId !== null && busyId !== row.candidate.id}
                onCompare={prod ? () => onCompare([prod.id, row.run_id]) : null}
                onPromote={(reason) => void promote(row, reason)}
              />
            ))}
          </ul>
        </>
      )}
      <MsgLine msg={msg} testId="reeval-promote-status" />
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: ReturnType<typeof deltaTone> }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className="num">{tone ? <Badge tone={tone}>{value}</Badge> : value}</dd>
    </div>
  )
}

function ReportRow({ row, overview, remote, busy, blocked, onCompare, onPromote }: {
  row: ReevalRow
  overview: ReevalOverview
  remote: boolean
  busy: boolean
  blocked: boolean
  onCompare: (() => void) | null
  onPromote: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  // The list's own copy of the candidate is the freshest (a decision since the run).
  const c = overview.candidates.find((x) => x.id === row.candidate.id) ?? row.candidate
  const name = modelLabel(c)
  const promotable = !remote && canPromote(c, rowFor(overview.report.rows, c.id)) && !sameModel(c, overview.production)
  return (
    <li>
      <div className="reeval-item-head">
        <strong>{name}</strong>
        <Badge tone={candidateStatusTone(c.status)}>{candidateStatusLabel(c.status)}</Badge>
        {row.status !== 'done' && <Badge tone={runStatusTone(row.status)}>{runStatusLabel(row.status)}</Badge>}
      </div>
      <dl className="reeval-stats">
        <Stat label="Score" value={formatScore(row.aggregate_score)} />
        <Stat label="Quality" value={formatDelta(row.quality_delta)} tone={deltaTone(row.quality_delta)} />
        <Stat label="Cost" value={formatCostDelta(row.cost_delta_usd)} tone={lowerIsBetterTone(row.cost_delta_usd, 0.00005)} />
        <Stat label="Avg time" value={formatLatencyDelta(row.latency_delta_seconds)} tone={lowerIsBetterTone(row.latency_delta_seconds, 0.0005)} />
        <Stat label="VRAM" value={formatVramDelta(row.vram_delta_mb)} tone={lowerIsBetterTone(row.vram_delta_mb, 0.5)} />
      </dl>
      {c.status !== 'candidate' && c.last_decision && <p className="reeval-summary">{c.last_decision.summary}</p>}
      <div className="actions">
        {onCompare && (
          <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Open ${name} against production in Arena`} onClick={onCompare}>
            Open in Arena
          </button>
        )}
      </div>
      {promotable && (
        <div className="reeval-promote">
          <ul className="reeval-effects muted">
            {promoteEffects(c, overview.production).map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
          <Field label="Reason for promoting" help="Optional. Kept with the decision in the history below.">
            <input value={reason} maxLength={MAX_REASON_CHARS} onChange={(e) => setReason(e.target.value)} placeholder="Optional" />
          </Field>
          <ConfirmButton
            name={name}
            label="Promote to production…"
            verb="promote"
            confirmLabel={`Confirm: make ${name} production`}
            tone="primary"
            busy={busy}
            disabled={blocked}
            ariaLabel={`Promote ${name} to production`}
            onConfirm={() => onPromote(reason)}
          />
        </div>
      )}
    </li>
  )
}

// ---- schedule ----

interface ScheduleEstimate {
  est: BenchmarkEstimate | null
  error: string | null
}

function ScheduleSection({ overview, options, sets, remote, savedEstimate, onSaved }: {
  overview: ReevalOverview
  options: BenchmarkOptions
  sets: BenchmarkSet[]
  remote: boolean
  savedEstimate: ScheduleEstimate | null
  onSaved: (o: ReevalSettingsSaved) => void
}) {
  const saved = overview.settings
  // Keyed on the saved settings by the parent, so a save starts a fresh draft.
  const [draft, setDraft] = useState<ScheduleDraft>(() => draftFromSettings(saved))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<Msg | null>(null)
  const set = (patch: Partial<ScheduleDraft>) => setDraft((d) => ({ ...d, ...patch }))
  const tierChoice = (options.tiers as string[]).includes(draft.tier) ? draft.tier : ''
  const choices = setOptions(sets, 'translation', tierChoice)
  // A saved set that no longer has cases stays pickable, so it isn't silently dropped.
  if (draft.setName && !choices.includes(draft.setName)) choices.push(draft.setName)
  const intervalErr = intervalProblem(draft.interval)
  const limitErr = limitProblem(draft.limit)
  const dirty = !intervalErr && !limitErr && scheduleDirty(draft, saved)
  const tierName = (t: string) => TIER_LABELS[t] ?? tierLabel(t)

  const save = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const o = await saveReevalSettings(scheduleBody(draft))
      onSaved(o)
    } catch (e) {
      setMsg({ tone: 'error', text: plainError(e, { pcOnly: true }) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Schedule and golden set" summary={scheduleSummary(saved, tierName)} storageKey="benchmark.reevalSchedule">
      <div className="setting-list">
        <Field
          label="Re-evaluate on a schedule"
          help="Off by default. When on, the PC re-runs production against every open candidate every so many days."
        >
          <Toggle checked={draft.enabled} disabled={remote} onChange={(v) => set({ enabled: v })} />
        </Field>
      </div>
      <p className="muted">
        A scheduled run spends money like Run now. To turn the schedule on, set a monthly cap in Settings or a limit per
        scheduled run below; a run that would pass either is skipped. The first run is one interval after you turn it on,
        and a run waits while other jobs are going. Nothing is promoted automatically: you read the report and decide.
      </p>
      <div className="field-row">
        <Field label="Every" unit="days" error={intervalErr}>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={365}
            value={draft.interval}
            disabled={remote}
            onChange={(e) => set({ interval: e.target.value })}
          />
        </Field>
        <Field label="Tier" help="Which golden sets a re-evaluation runs on.">
          <select value={tierChoice} disabled={remote} onChange={(e) => set({ tier: e.target.value, setName: '' })}>
            <option value="">Any tier</option>
            {options.tiers.map((t) => (
              <option key={t} value={t}>{tierName(t)}</option>
            ))}
          </select>
        </Field>
        <Field label="Golden set">
          <select value={draft.setName} disabled={remote} onChange={(e) => set({ setName: e.target.value })}>
            <option value="">Any set</option>
            {choices.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Field>
        <Field
          label="Limit per scheduled run"
          unit="USD"
          help="A scheduled run estimated above this is skipped, and the report says so. Leave blank only if a monthly cap is set in Settings: the schedule needs one or the other. Run now shows its own estimate instead."
          error={limitErr}
        >
          <input
            inputMode="decimal"
            value={draft.limit}
            disabled={remote}
            placeholder="No limit"
            onChange={(e) => set({ limit: e.target.value })}
          />
        </Field>
      </div>
      <p className="num" data-testid="reeval-next-due">{nextDueText(overview)}</p>
      {savedEstimate?.est ? (
        <>
          <p className="muted">Each scheduled run, estimated as things stand now:</p>
          <EstimateBlock est={savedEstimate.est} stage="translation" />
        </>
      ) : savedEstimate?.error ? (
        <p className="muted" data-testid="reeval-schedule-estimate">No estimate for a scheduled run yet: {savedEstimate.error}</p>
      ) : null}
      {remote ? (
        <p className="muted">{PC_ONLY_BODY}</p>
      ) : (
        <div className="actions">
          <button type="button" className={buttonClass('secondary')} disabled={!dirty || busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save schedule'}
          </button>
          {dirty && setChoiceDirty(draft, saved) && <span className="muted">Estimates and runs use the saved golden set.</span>}
        </div>
      )}
      <MsgLine msg={msg} />
    </Section>
  )
}

// ---- history ----

function HistorySection({ decisions, candidates }: { decisions: ModelDecision[] | null; candidates: ModelCandidate[] }) {
  const n = decisions?.length ?? 0
  return (
    <Section
      title="Decision history"
      count={decisions ? n : undefined}
      summary={decisions === null ? 'Could not load' : n ? 'Newest first' : 'None yet'}
      storageKey="benchmark.reevalHistory"
    >
      {decisions === null ? (
        <p className="muted">The decision history could not be loaded.</p>
      ) : n === 0 ? (
        <p className="muted">No candidate has been promoted or rejected yet.</p>
      ) : (
        <ul className="reeval-list" aria-label="Decisions">
          {decisions.map((d) => {
            const c = candidates.find((x) => x.id === d.candidate_id)
            const scores = decisionScores(d.scores)
            return (
              <li key={d.id}>
                <div className="reeval-item-head">
                  <strong>{c ? modelLabel(c) : `Candidate ${d.candidate_id}`}</strong>
                  <Badge tone={d.decision === 'promoted' ? 'ok' : 'neutral'}>{decisionLabel(d.decision)}</Badge>
                  <span className="muted num">{formatWhen(d.decided_at)}</span>
                </div>
                <p>{d.reason || <span className="muted">No reason given.</span>}</p>
                {scores && <p className="muted num">{scores}</p>}
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
