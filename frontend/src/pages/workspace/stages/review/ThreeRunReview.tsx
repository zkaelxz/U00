import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { withSignal } from '../../../../api/client'
import { listAllLines, restoreSnapshot } from '../../../../api/restructure'
import { beginThreeRun, captureThreeRun, endThreeRun, getThreeRun, keepRestThreeRun, pickThreeRun } from '../../../../api/threeRun'
import { getTranslateConfig, getTranslateEstimate, startTranslateRun } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useCancelsAnyJob } from '../../../../hooks/useSession'
import { cancelHint, isTerminalStatus, offersCancel, type JobRecord } from '../../../../types/jobs'
import type { ThreeRunState } from '../../../../types/threeRun'
import type { TranslateRunConfig, TranslateRunEstimate } from '../../../../types/translateStage'
import { applyStartToForm, buildRunBody, initialForm, loadPresetStart, validateRun } from '../../translateForm'
import { LineAiPicker } from './LineAiPicker'
import { useLineAiPick } from './lineAiPick'
import { useLineSelectionContext } from './LineSelectionContext'
import { formatUsd } from './strongerEngineLogic'

const RUNS = 3

// Optional: translates the lines three times through the ordinary Translate run
// (the server records each run) and then lists only the lines the runs disagree on, so
// each can be picked at a glance. Lines the runs agree on keep the last run's
// English. The title's saved settings, glossary and context apply as for any
// run; a snapshot taken first is the undo for the whole review.
export function ThreeRunReview({ dramaId, jobRunning, onChanged }: { dramaId: number; jobRunning: boolean; onChanged: () => void }) {
  const { selectedIds } = useLineSelectionContext()
  const picker = useLineAiPick(dramaId)
  const anyJob = useCancelsAnyJob()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [review, setReview] = useState<ThreeRunState | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const [runNo, setRunNo] = useState(0)
  const [current, setCurrent] = useState(0)
  const [undoStack, setUndoStack] = useState<{ lineId: number; prev: number | null }[]>([])
  const [jobId, setJobId, runKey] = useJobRun()
  const targets = useRef<number[] | null>(null)
  const { engine, model } = picker.pick
  const key = selectedIds.join(',')

  const reload = useCallback(() => {
    getThreeRun(dramaId).then(setReview, setError)
  }, [dramaId])

  useEffect(() => {
    setReview(null)
    setRunNo(0)
    setUndoStack([])
    const ctl = new AbortController()
    getTranslateConfig(dramaId, withSignal(ctl.signal)).then((c) => !ctl.signal.aborted && setConfig(c), () => undefined)
    // A review left open (or half-recorded) is picked up again, e.g. after a reload.
    getThreeRun(dramaId, withSignal(ctl.signal)).then((s) => !ctl.signal.aborted && s.active && setReview(s), () => undefined)
    return () => ctl.abort()
  }, [dramaId])

  useEffect(() => {
    setEstimate(null)
    const ctl = new AbortController()
    getTranslateEstimate(dramaId, {
      engine: engine || undefined, model: model || undefined, force_retranslate: true,
      ...(selectedIds.length ? { line_ids: selectedIds } : {}),
    }, withSignal(ctl.signal)).then((e) => !ctl.signal.aborted && setEstimate(e), () => undefined)
    return () => ctl.abort()
    // selectedIds is rebuilt each render; `key` stands for its content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, key, engine, model])

  const startRun = useCallback((n: number, lineIds: number[]) => {
    if (!config) return
    const form = {
      ...applyStartToForm(initialForm(config, loadPresetStart(dramaId)), config.translate_start, config),
      engine, model, reflect: false, bulk: false, fallbacks: [] as string[], force: true, forceConfirmed: true,
    }
    const problem = validateRun(form, config.translation_engine, config.bulk_supported_engines)
    if (problem) {
      setError(new Error(problem))
      setRunNo(0)
      return
    }
    setRunNo(n)
    startTranslateRun(dramaId, { ...buildRunBody(form), line_ids: lineIds, own_lines_only: true })
      .then((r) => setJobId(r.job_id), (e) => { setError(e); setRunNo(0) })
  }, [config, dramaId, engine, model, setJobId])

  const onRunDone = (job: JobRecord) => {
    if (job.status !== 'done') {
      setError(new Error(job.message || `Run ${runNo} ${job.status}. Nothing was recorded for it.`))
      setRunNo(0)
      return
    }
    captureThreeRun(dramaId, runNo).then((s) => {
      setReview(s)
      onChanged()
      if (runNo < RUNS && targets.current) startRun(runNo + 1, targets.current)
      else setRunNo(0)
    }, (e) => { setError(e); setRunNo(0) })
  }
  const { job, error: pollError } = useJob(jobId, { runKey, onDone: onRunDone })

  const active = !!job && !isTerminalStatus(job.status) && runNo > 0
  const busy = starting || runNo > 0
  const blocked = jobRunning && !busy ? 'Another job is running on this title. Try again when it finishes.' : null

  useEffect(() => setConfirming(false), [key, engine, model])

  const start = () => {
    setConfirming(false)
    setError(null)
    setStarting(true)
    setUndoStack([])
    setCurrent(0)
    beginThreeRun(dramaId, selectedIds.length ? selectedIds : null)
      .then((b) => {
        targets.current = b.line_ids
        setReview({ active: true, runs_done: 0, history_id: b.history_id, line_count: b.line_ids.length, items: [] })
        startRun(1, b.line_ids)
      }, setError)
      .finally(() => setStarting(false))
  }

  const items = review?.items ?? []
  const left = items.filter((i) => i.picked_run == null).length
  const reviewing = !!review?.active && review.runs_done === RUNS && runNo === 0
  const shown = items[Math.min(current, Math.max(items.length - 1, 0))]

  const pick = (run: number) => {
    if (!shown) return
    const prev = shown.picked_run ?? null
    pickThreeRun(dramaId, [{ line_id: shown.line_id, run }]).then((r) => {
      if (!r.applied.length) setError(new Error('That line was edited since the runs, so it was left as it is.'))
      else setUndoStack((s) => [...s, { lineId: shown.line_id, prev }])
      reload()
      onChanged()
      setCurrent((c) => Math.min(c + 1, items.length - 1))
    }, setError)
  }
  const undoLast = () => {
    const last = undoStack[undoStack.length - 1]
    if (!last) return
    // Unpicked lines hold the last run's English, so undoing the first pick restores run 3.
    pickThreeRun(dramaId, [{ line_id: last.lineId, run: last.prev ?? RUNS }]).then(() => {
      setUndoStack((s) => s.slice(0, -1))
      const at = items.findIndex((i) => i.line_id === last.lineId)
      if (at >= 0) setCurrent(at)
      reload()
      onChanged()
    }, setError)
  }
  const keepRest = (run: number) => {
    keepRestThreeRun(dramaId, run).then(() => { reload(); onChanged() }, setError)
  }
  const restoreAll = () => {
    if (!review?.history_id) return
    const historyId = review.history_id
    listAllLines(dramaId)
      .then((all) => restoreSnapshot(dramaId, historyId, all.map((l) => l.id)))
      .then(() => endThreeRun(dramaId))
      .then(() => { setReview(null); onChanged() }, setError)
  }
  const close = () => {
    endThreeRun(dramaId).then(() => { setReview(null); setUndoStack([]) }, setError)
  }

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement
    if (e.metaKey || e.ctrlKey || e.altKey || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return
    if (e.key === '1' || e.key === '2' || e.key === '3') pick(Number(e.key))
    else if (e.key === 'j' || e.key === 'ArrowDown') setCurrent((c) => Math.min(c + 1, items.length - 1))
    else if (e.key === 'k' || e.key === 'ArrowUp') setCurrent((c) => Math.max(c - 1, 0))
    else if (e.key === 'z') undoLast()
    else return
    e.preventDefault()
  }

  const count = selectedIds.length || (review?.line_count ?? 0)
  const per = estimate?.estimated_usd
  return (
    <Section storageKey="review.threeRun" title="Run 3 times, review differences" summary="Translate three times, then pick between the lines the runs disagree on">
      <div className="compare-panel" data-testid="three-run">
        {!review && (
          <>
            <p className="muted">
              Translates the ticked lines (every line when none is ticked) three times with this title’s settings, then shows only the
              lines where the runs differ so you can pick one. Lines all three agree on keep the last run’s English. It replaces their
              English, hand edits included; a snapshot is taken first.
            </p>
            <LineAiPicker picker={picker} disabled={busy} />
            <p data-testid="three-run-estimate">
              {selectedIds.length ? `${selectedIds.length} line${selectedIds.length === 1 ? '' : 's'} ticked` : 'All lines'}
              {per != null && ` · 3 runs, estimate ${formatUsd(per * RUNS)}`}
            </p>
            <div className="review-actions">
              {confirming && !busy ? (
                <>
                  <button type="button" onClick={start} data-testid="three-run-confirm">
                    Run 3 times{per != null ? ` for about ${formatUsd(per * RUNS)}` : ''}
                  </button>
                  <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setConfirming(false)}>Cancel</button>
                </>
              ) : (
                <button type="button" onClick={() => setConfirming(true)} disabled={busy || !config || !!blocked}>Run 3 times…</button>
              )}
            </div>
            {blocked && <p className="muted">{blocked}</p>}
          </>
        )}
        {review && runNo > 0 && (
          <div className="review-actions" data-testid="three-run-progress">
            <p className="muted">Run {runNo} of {RUNS}: {job?.message || 'Working…'} ({count} lines)</p>
            {active && job && offersCancel(job, anyJob) && (
              <button type="button" className={buttonClass('secondary', 'sm')} title={cancelHint(job.job_id)} onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>
            )}
          </div>
        )}
        {review && runNo === 0 && !reviewing && (
          <div className="review-actions">
            <p className="muted">Only {review.runs_done} of {RUNS} runs were recorded, so there is nothing to compare.</p>
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={restoreAll}>Restore the English from before</button>
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={close}>Close</button>
          </div>
        )}
        {reviewing && (
          <div onKeyDown={onKey} tabIndex={0} data-testid="three-run-review" aria-label="Pick between the three runs">
            {items.length === 0 ? (
              <p role="status">All three runs agree on every line. Nothing to pick.</p>
            ) : (
              <>
                <p role="status" data-testid="three-run-left">
                  {left} of {items.length} differing lines left <span className="muted">· line {(shown?.idx ?? 0) + 1}</span>
                </p>
                {shown && (
                  <div data-testid="three-run-line">
                    <p lang="zh">{shown.zh}</p>
                    {shown.versions.map((v, i) => (
                      <button key={i} type="button" className={buttonClass(shown.picked_run === i + 1 ? 'primary' : 'secondary')}
                        style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 4 }}
                        data-testid={`three-run-version-${i + 1}`} onClick={() => pick(i + 1)}>
                        <strong>{i + 1}</strong> {v || <em>(empty)</em>}
                      </button>
                    ))}
                  </div>
                )}
                <div className="review-actions">
                  <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setCurrent((c) => Math.max(c - 1, 0))}>Previous (K)</button>
                  <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setCurrent((c) => Math.min(c + 1, items.length - 1))}>Next (J)</button>
                  <button type="button" className={buttonClass('secondary', 'sm')} disabled={!undoStack.length} onClick={undoLast}>Undo last pick (Z)</button>
                  {[1, 2, 3].map((r) => (
                    <button key={r} type="button" className={buttonClass('secondary', 'sm')} disabled={!left} onClick={() => keepRest(r)} data-testid={`three-run-keep-${r}`}>
                      Keep run {r} for the rest
                    </button>
                  ))}
                </div>
                <p className="muted">Keys: 1, 2, 3 pick this line and move on · J / K next / previous · Z undo.</p>
              </>
            )}
            <div className="review-actions">
              <button type="button" onClick={close} data-testid="three-run-done">Done</button>
              <button type="button" className={buttonClass('secondary', 'sm')} onClick={restoreAll}>Restore the English from before the review</button>
            </div>
          </div>
        )}
        <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
      </div>
    </Section>
  )
}
