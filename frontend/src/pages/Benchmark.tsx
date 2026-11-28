/*
 * Benchmark Lab (#/benchmark): golden sets, persistent
 * per-run results and the Model Arena. Reached from Diagnostics (its nav
 * item stays active here). Reads work anywhere the viewer may see
 * Diagnostics; importing, adding and deleting cases and starting a run are
 * PC only (the server enforces; the page hides them in remote mode). The
 * Model re-evaluation card runs production against candidate
 * models, and the Settings sweep runs its passes, through the same benchmark_lab job.
 * Its sections are listed down the left, one shown at a time.
 * Behind Developer Mode: opened by URL with the mode off, the page shows only a link to its card in Settings.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { getBenchmarkOptions, getBenchmarkSets, listBenchmarkRuns } from '../api/benchmark'
import type { BenchmarkOptions, BenchmarkRun, BenchmarkRunStarted, BenchmarkSet } from '../types/benchmark'
import { cancelJob } from '../api/jobs'
import { PageShell } from '../components/PageShell'
import { DeveloperModeOff } from '../components/DeveloperModeOff'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { FlatPaneContext } from '../components/flatPane'
import { HelpTip } from '../components/HelpTip'
import { SidePane } from '../components/SidePane'
import { Tabs } from '../components/Tabs'
import { WarnBar } from '../components/WarnBar'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { buttonClass } from '../components/uiClasses'
import { useJob, useJobRun } from '../hooks/useJob'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import { routeHref, useRoute } from '../router'
import { jobFailed, jobOutcomeText } from '../types/jobs'
import { useDeveloperModeKnown } from './assistant/developerMode'
import { ArenaView, type ArenaTarget } from './benchmark/ArenaView'
import { AddCasesPanel, GoldenSetsCard } from './benchmark/GoldenSetsCard'
import { ReevalCard } from './benchmark/ReevalCard'
import { RunCard } from './benchmark/RunCard'
import { HandRunResultsCard } from './benchmark/HandRunResultsCard'
import { ModelSummaryCard } from './benchmark/ModelSummaryCard'
import { RunsCard } from './benchmark/RunsCard'
import { SweepCard } from './benchmark/SweepCard'
import { TranslateSweepCard } from './benchmark/TranslateSweepCard'
import { BlindStart } from './benchmark/BlindStart'
import { BENCH_INTRO, BENCH_PAGE_HELP } from './benchmark/benchmarkHelp'
import { isRunActive, runningStatus } from './benchmark/benchmarkForm'
import { QualitySection } from './diagnostics/QualitySection'
import { BENCHMARK_TABS, isBenchmarkTab, type BenchmarkTab } from './benchmark/benchmarkTabs'
import './benchmark/benchmark.css'

const RUNS_LIMIT = 50
const SHOWING = { showing: true }
const HIDDEN = { showing: false }

function BenchmarkContent({ compare }: { compare?: string }) {
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
  const [blindNote, setBlindNote] = useState<string | null>(null)

  const route = useRoute()
  // A compare link's engines are prefilled in Run a benchmark, so that is the tab it lands on.
  const linkTab = route.name === 'benchmark' ? (route.tab ?? (route.compare ? 'run' : undefined)) : undefined
  const [storedTab, setStoredTab] = usePersistedState<BenchmarkTab>('benchmark.tab', 'sets')
  // A link's tab wins over the remembered one from the first render, so the tab it names is what shows.
  const [tab, setTabState] = useState<BenchmarkTab>(() => linkTab ?? (isBenchmarkTab(storedTab) ? storedTab : 'sets'))
  const selectTab = useCallback((t: BenchmarkTab) => {
    setTabState(t)
    setStoredTab(t)
  }, [setStoredTab])
  const [seenLinkTab, setSeenLinkTab] = useState(linkTab)
  if (linkTab !== seenLinkTab) {
    setSeenLinkTab(linkTab)
    if (linkTab) setTabState(linkTab)
  }
  useEffect(() => {
    if (linkTab) setStoredTab(linkTab)
  }, [linkTab, setStoredTab])
  // Away from the main PC the PC-only pane would be empty; a link to it lands on Golden sets.
  const shownTabs = BENCHMARK_TABS.filter((t) => !(t.pcOnly && pc === 'remote'))
  const shownTab = shownTabs.some((t) => t.id === tab) ? tab : 'sets'
  // Hidden, not unmounted, so form entries and a run's polling survive a tab change.
  const panel = (id: BenchmarkTab, children: ReactNode) => (
    <div key={id} id={tabPanelId('benchmark', id)} role="tabpanel" aria-labelledby={tabButtonId('benchmark', id)} className="bench-panel" hidden={shownTab !== id}>
      <FlatPaneContext.Provider value={shownTab === id ? SHOWING : HIDDEN}>{children}</FlatPaneContext.Provider>
    </div>
  )

  const loadSets = useCallback(() => {
    getBenchmarkSets().then((r) => setSets(r.sets), setError)
  }, [])
  // A slow response for an older stage filter or an earlier poll must not replace a newer list.
  const runsSeq = useRef(0)
  const loadRuns = useCallback(() => {
    const seq = ++runsSeq.current
    listBenchmarkRuns({ stage: runStage || null, limit: RUNS_LIMIT }).then(
      (r) => {
        if (seq === runsSeq.current) setRuns(r.runs)
      },
      (e: unknown) => {
        if (seq === runsSeq.current) setError(e)
      },
    )
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
  // Dropping the dead id lets the active-run effect above pick the job up again if it is still going.
  const reconnect = () => {
    setJobId(null)
    loadRuns()
  }
  const stop = () => {
    if (jobId) cancelJob(jobId).then(() => undefined, setError)
  }

  const finishedIds = started && done ? started.session_ids : null
  const finishedText = done && job ? (jobFailed(job) ? jobOutcomeText(job) ?? 'The run failed.' : 'Run finished.') : null

  return (
    <PageShell
      width="standard"
      title={(
        <>
          Benchmark Lab
          <HelpTip label="Benchmark Lab" id="bench-page-help" className="bench-help">
            {BENCH_INTRO.map((line) => (
              <span key={line} className="bench-help-step">{line}</span>
            ))}
            {BENCH_PAGE_HELP.map((line, i) => (
              <span key={line} className="bench-help-step">
                {i + 1}. {line}
              </span>
            ))}
          </HelpTip>
        </>
      )}
      label="Benchmark Lab"
      className="bench-page"
      actions={(
        <>
          <BlindStart onStart={(ids) => setTarget({ kind: 'arena', runIds: ids })} onNote={setBlindNote} />
          <ButtonLink href={routeHref({ name: 'diagnostics' })} variant="ghost" size="sm" aria-label="Back to Diagnostics" title="Back to Diagnostics">
            <span aria-hidden="true">‹</span>
          </ButtonLink>
        </>
      )}
      meta={(
        <p className="page-meta muted" data-testid="bench-meta">
          {options
            ? `Pass mark ${Math.round(options.pass_threshold * 100)}% (${Math.round(options.chrf_pass_threshold * 100)}% for chrF) · Up to ${options.max_configs} engines per run`
            : 'Loading…'}
        </p>
      )}
    >
      {pc === 'remote' && <WarnBar testId="bench-view-only">View only: runs start on the main PC</WarnBar>}
      {blindNote && <p className="status-line" role="status" data-testid="blind-start">{blindNote}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {running && (
        <p className="status-line" role="status" data-testid="bench-running">{runningStatus(job)}</p>
      )}

      {jobError && jobId && !done && (
        <p className="status-line" role="alert" data-testid="bench-lost-job">
          <span>Lost contact with the running benchmark. It may still be going on the PC.</span>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={reconnect}>
            Reconnect
          </button>
        </p>
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

      <SidePane nav={<Tabs side label="Benchmark Lab" idPrefix="benchmark" tabs={shownTabs} active={shownTab} onSelect={selectTab} />}>
      {panel('quality', <QualitySection />)}
      {panel('sets', options && sets && <GoldenSetsCard sets={sets} pc={pc} phone={phone} onChanged={loadSets} />)}
      {pc !== 'remote' && panel('add', options && sets && <AddCasesPanel sets={sets} options={options} pc={pc} onChanged={loadSets} />)}
      {panel('run', options && sets && (
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
      ))}
      {panel('sweep', sets && (
        <SweepCard
          sets={sets}
          pc={pc}
          job={job}
          running={running}
          onStarted={(id) => {
            setStarted(null)
            setJobId(id)
          }}
          onStop={stop}
          onCompare={(ids) => setTarget({ kind: 'arena', runIds: ids })}
        />
      ))}
      {panel('tsweep', options && sets && (
        <TranslateSweepCard
          options={options}
          sets={sets}
          pc={pc}
          job={job}
          running={running}
          onStarted={(id) => {
            setStarted(null)
            setJobId(id)
          }}
          onStop={stop}
          onCompare={(ids) => setTarget({ kind: 'arena', runIds: ids })}
        />
      ))}
      {panel('reeval', options && sets && (
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
      ))}
      {panel('runs', (
        <>
          {runs && <ModelSummaryCard runs={runs} />}
          <HandRunResultsCard />
          {runs && (
            <RunsCard
              runs={runs}
              stageFilter={runStage}
              onStageFilter={setRunStage}
              maxCompare={options?.max_configs ?? 4}
              phone={phone}
              onCompare={(ids) => setTarget({ kind: 'arena', runIds: ids })}
              onOpen={(id) => setTarget({ kind: 'run', runId: id })}
              canDelete={pc !== 'remote'}
              onDeleted={(id) => {
                // A results view of a deleted run would only show an error.
                setTarget((t) => (t && (t.kind === 'run' ? t.runId === id : t.runIds.includes(id)) ? null : t))
                loadRuns()
              }}
            />
          )}
        </>
      ))}
      {target && <ArenaView target={target} phone={phone} onClose={() => setTarget(null)} />}

      {!options && !error && <p className="muted">Loading…</p>}
      </SidePane>
    </PageShell>
  )
}

/** compare: the raw ?compare= value of a Model health link; RunCard applies it once. */
export default function BenchmarkPage({ compare }: { compare?: string } = {}) {
  const developerMode = useDeveloperModeKnown()
  const pc = usePcOnly()
  // Developer Mode is a setting of the PC that another device cannot read; there the page stays read-only as before.
  if (developerMode === true || pc === 'remote') return <BenchmarkContent compare={compare} />
  return (
    <PageShell width="standard" title="Benchmark Lab" label="Benchmark Lab" className="bench-page">
      {developerMode === null ? <p className="muted">Loading…</p> : <DeveloperModeOff />}
    </PageShell>
  )
}
