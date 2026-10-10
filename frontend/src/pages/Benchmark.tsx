/*
 * Benchmark Lab (#/benchmark): golden sets, persistent
 * per-run results and the Model Arena. Reached from Diagnostics (its nav
 * item stays active here). Reads work anywhere the viewer may see
 * Diagnostics; importing, adding and deleting cases and starting a run are
 * PC only (the server enforces; the page hides them in remote mode). The
 * Model re-evaluation card runs production against candidate
 * models through the same benchmark_lab job.
 */
import { useCallback, useEffect, useState } from 'react'

import {
  getBenchmarkOptions, getBenchmarkSets, listBenchmarkRuns,
  type BenchmarkOptions, type BenchmarkRun, type BenchmarkRunStarted, type BenchmarkSet,
} from '../api/benchmark'
import { cancelJob } from '../api/jobs'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { HelpTip } from '../components/HelpTip'
import { buttonClass } from '../components/uiClasses'
import { useJob, useJobRun } from '../hooks/useJob'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import { routeHref } from '../router'
import { jobFailed, jobOutcomeText } from '../types/jobs'
import { ArenaView, type ArenaTarget } from './benchmark/ArenaView'
import { GoldenSetsCard } from './benchmark/GoldenSetsCard'
import { ReevalCard } from './benchmark/ReevalCard'
import { RunCard } from './benchmark/RunCard'
import { RunsCard } from './benchmark/RunsCard'
import { BENCH_INTRO, BENCH_PAGE_HELP } from './benchmark/benchmarkHelp'
import { isRunActive } from './benchmark/benchmarkForm'
import './benchmark/benchmark.css'

const RUNS_LIMIT = 50

/** compare: the raw ?compare= value of a Model health link; RunCard applies it once. */
export default function BenchmarkPage({ compare }: { compare?: string } = {}) {
  const pc = usePcOnly()
  const phone = useMediaQuery('(max-width: 640px)')
  const [options, setOptions] = useState<BenchmarkOptions | null>(null)
  const [sets, setSets] = useState<BenchmarkSet[] | null>(null)
  const [runs, setRuns] = useState<BenchmarkRun[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [runStage, setRunStage] = usePersistedState<string>('benchmark.runsStage', '')
  const [target, setTarget] = useState<ArenaTarget | null>(null)
  const [started, setStarted] = useState<BenchmarkRunStarted | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()

  const loadSets = useCallback(() => {
    getBenchmarkSets().then((r) => setSets(r.sets), setError)
  }, [])
  const loadRuns = useCallback(() => {
    listBenchmarkRuns({ stage: runStage || null, limit: RUNS_LIMIT }).then((r) => setRuns(r.runs), setError)
  }, [runStage])

  useEffect(() => {
    getBenchmarkOptions().then(setOptions, setError)
    loadSets()
  }, [loadSets])
  useEffect(loadRuns, [loadRuns])

  const { job, done, error: jobError } = useJob(jobId, { runKey, onDone: () => loadRuns() })
  // A job that can't be read (gone after a restart) is not "running".
  const running = !!jobId && !done && !jobError
  // Opened while a run is still going (e.g. after a reload): follow its job.
  const activeRun = !!runs?.some(isRunActive)
  useEffect(() => {
    if (activeRun && !jobId) setJobId('benchmark_lab')
  }, [activeRun, jobId, setJobId])

  const onStarted = (res: BenchmarkRunStarted) => {
    setStarted(res)
    setJobId(res.job_id)
    loadRuns()
  }
  const stop = () => {
    if (jobId) cancelJob(jobId).then(() => undefined, setError)
  }

  const finishedIds = started && done ? started.session_ids : null
  const finishedText = done && job ? (jobFailed(job) ? jobOutcomeText(job) ?? 'The run failed.' : 'Run finished.') : null

  return (
    <section className="bench-page" aria-label="Benchmark Lab">
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">
            Benchmark Lab
            <HelpTip label="Benchmark Lab" id="bench-page-help" className="bench-help">
              {BENCH_PAGE_HELP.map((line, i) => (
                <span key={line} className="bench-help-step">
                  {i + 1}. {line}
                </span>
              ))}
            </HelpTip>
          </h2>
          <p className="page-meta pill-row">
            {options ? (
              <>
                <Badge tone="neutral">Pass mark {Math.round(options.pass_threshold * 100)}% ({Math.round(options.chrf_pass_threshold * 100)}% for chrF)</Badge>
                <Badge tone="neutral">Up to {options.max_configs} engines per run</Badge>
                {pc === 'remote' && <Badge tone="warn">View only: runs start on the main PC</Badge>}
              </>
            ) : (
              'Loading…'
            )}
          </p>
        </div>
        <ButtonLink href={routeHref({ name: 'diagnostics' })} variant="ghost" size="sm">
          ‹ Diagnostics
        </ButtonLink>
      </header>
      <div className="muted bench-intro" data-testid="bench-intro">
        {BENCH_INTRO.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {options && sets && <GoldenSetsCard sets={sets} options={options} pc={pc} phone={phone} onChanged={loadSets} />}
      {options && sets && (
        <RunCard
          options={options}
          sets={sets}
          pcRemote={pc === 'remote'}
          job={job}
          running={running}
          onStarted={onStarted}
          onStop={stop}
          compare={compare}
        />
      )}
      {options && sets && (
        <ReevalCard
          options={options}
          sets={sets}
          pc={pc}
          phone={phone}
          job={job}
          running={running}
          onStarted={onStarted}
          onStop={stop}
          onCompare={(ids) => setTarget({ kind: 'arena', runIds: ids })}
        />
      )}
      {finishedText && (
        <p className="status-line" role="status" data-testid="bench-finished">
          <span>{finishedText}</span>
          {finishedIds && finishedIds.length >= 2 && (
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setTarget({ kind: 'arena', runIds: finishedIds.slice(0, 4) })}>
              Compare these runs
            </button>
          )}
          {finishedIds && finishedIds.length === 1 && (
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setTarget({ kind: 'run', runId: finishedIds[0] })}>
              See results
            </button>
          )}
        </p>
      )}

      {runs && (
        <RunsCard
          runs={runs}
          stageFilter={runStage}
          onStageFilter={setRunStage}
          maxCompare={options?.max_configs ?? 4}
          phone={phone}
          onCompare={(ids) => setTarget({ kind: 'arena', runIds: ids })}
          onOpen={(id) => setTarget({ kind: 'run', runId: id })}
        />
      )}

      {target && <ArenaView target={target} phone={phone} onClose={() => setTarget(null)} />}

      {!options && !error && <p className="muted">Loading…</p>}
    </section>
  )
}
