import { useEffect, useRef, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { patchLine } from '../../../../api/review'
import { listAllLines } from '../../../../api/restructure'
import { lineNumber } from '../../../../lineNumber'
import { withSignal } from '../../../../api/client'
import { getTranslateConfig, getTranslateEstimate, startTranslateRun } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useCancelsAnyJob } from '../../../../hooks/useSession'
import { cancelHint, isTerminalStatus, offersCancel } from '../../../../types/jobs'
import type { TranslateRunConfig, TranslateRunEstimate } from '../../../../types/translateStage'
import { applyStartToForm, buildRunBody, initialForm, loadPresetStart, validateRun } from '../../translateForm'
import { LineAiPicker } from './LineAiPicker'
import { useLineAiPick } from './lineAiPick'
import { useLineSelectionContext } from './useLineSelectionContext'
import { formatUsd } from './strongerEngineLogic'

// Redoes the ticked lines with an engine and model picked here, as an ordinary
// Translate run limited to those lines: same glossary, style, characters, pronoun
// settings and context windows (taken from the title's saved run settings),
// with the lines around them as context. It replaces the ticked lines' English,
// hand edits included, so the button asks to confirm first; a line edited while
// the run works keeps the edit whatever the options.
export function RetranslateSelected({ dramaId, jobRunning, onChanged }: { dramaId: number; jobRunning: boolean; onChanged: () => void }) {
  const { selectedIds, selectMany, clear } = useLineSelectionContext()
  const [rangeError, setRangeError] = useState<string | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  // English before the run, so the result can be read as before/after and a line put back.
  const before = useRef<Map<number, string>>(new Map())
  const [showAll, setShowAll] = useState(false)
  const [putBackCount, setPutBackCount] = useState(0)
  const [pendingBack, setPendingBack] = useState<number | null>(null)
  const [changes, setChanges] = useState<{ id: number; number: number; before: string; after: string }[] | null>(null)
  const picker = useLineAiPick(dramaId)
  const anyJob = useCancelsAnyJob()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      onChanged()
      listAllLines(dramaId).then((all) => setChanges(
        all.flatMap((l) => (before.current.has(l.id) && (before.current.get(l.id) ?? '') !== (l.en ?? '')
          ? [{ id: l.id, number: lineNumber(l.idx), before: before.current.get(l.id) ?? '', after: l.en ?? '' }] : [])),
      ), () => undefined)
    },
  })

  const tickRange = () => {
    const x = parseInt(from, 10)
    const y = parseInt(to, 10)
    if (!(x >= 1) || !(y >= 1)) {
      setRangeError('Enter two line numbers, such as 12 and 40.')
      return
    }
    const a = Math.min(x, y)
    const b = Math.max(x, y)
    setRangeError(null)
    listAllLines(dramaId).then((all) => {
      const ids = all.filter((l) => lineNumber(l.idx) >= a && lineNumber(l.idx) <= b).map((l) => l.id)
      if (ids.length === 0) setRangeError(`No lines are numbered ${a} to ${b}.`)
      else { clear(); selectMany(ids) }
    }, setError)
  }
  const putBack = (c: { id: number; before: string; after: string }) => {
    setPendingBack(c.id)
    patchLine(dramaId, c.id, { en: c.before, expected: { en: c.after } }).then(() => {
      setChanges((cur) => cur && cur.filter((x) => x.id !== c.id))
      setPutBackCount((n) => n + 1)
      onChanged()
    }, setError).finally(() => setPendingBack(null))
  }

  useEffect(() => {
    const ctl = new AbortController()
    getTranslateConfig(dramaId, withSignal(ctl.signal)).then((c) => !ctl.signal.aborted && setConfig(c), () => undefined)
    return () => ctl.abort()
  }, [dramaId])

  const { engine, model } = picker.pick
  const key = selectedIds.join(',')
  useEffect(() => {
    setEstimate(null)
    if (!key) return
    const ctl = new AbortController()
    getTranslateEstimate(dramaId, { engine: engine || undefined, model: model || undefined, force_retranslate: true, line_ids: selectedIds }, withSignal(ctl.signal))
      .then((e) => !ctl.signal.aborted && setEstimate(e), () => undefined)
    return () => ctl.abort()
    // selectedIds is rebuilt each render; `key` stands for its content.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, key, engine, model])

  const active = !!job && !isTerminalStatus(job.status)
  const busy = starting || active
  const blocked = selectedIds.length === 0
    ? 'Tick the lines to redo first.'
    : jobRunning && !busy ? 'Another job is running on this title. Try again when it finishes.' : null

  useEffect(() => setConfirming(false), [key, engine, model])

  const start = () => {
    if (!config) return
    setConfirming(false)
    const form = {
      ...applyStartToForm(initialForm(config, loadPresetStart(dramaId)), config.translate_start, config),
      engine, model, reflect: false, bulk: false, fallbacks: [] as string[], force: true, forceConfirmed: true,
    }
    const problem = validateRun(form, config.translation_engine, config.bulk_supported_engines)
    if (problem) {
      setError(new Error(problem))
      return
    }
    setError(null)
    setStarting(true)
    setChanges(null)
    setPutBackCount(0)
    setShowAll(false)
    listAllLines(dramaId).then((all) => { before.current = new Map(all.filter((l) => selectedIds.includes(l.id)).map((l) => [l.id, l.en ?? ''])) }, () => undefined)
    startTranslateRun(dramaId, { ...buildRunBody(form), line_ids: selectedIds, own_lines_only: true })
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  return (
    <Section storageKey="review.retranslateSelected" title="Re-translate selected lines" summary="Redoes the ticked lines with the engine and model you pick">
      <div className="compare-panel" data-testid="retranslate-selected">
        <p className="muted">
          Runs a normal translation on just the ticked lines, with this title’s glossary, style, characters and context
          settings, and the lines around them as context. It replaces their English, including hand edits; the earlier text stays in History to restore.
        </p>
        <form className="review-actions" data-testid="retranslate-range" onSubmit={(e) => { e.preventDefault(); tickRange() }}>
          <label>From line <input inputMode="numeric" pattern="[0-9]*" enterKeyHint="go" autoComplete="off" size={5} value={from} disabled={busy} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>to line <input inputMode="numeric" pattern="[0-9]*" enterKeyHint="go" autoComplete="off" size={5} value={to} disabled={busy} onChange={(e) => setTo(e.target.value)} /></label>
          <button type="submit" className={buttonClass('secondary', 'sm')} disabled={busy || !from || !to}>
            {from && to ? `Tick lines ${Math.min(+from, +to) || from}–${Math.max(+from, +to) || to}` : 'Tick these lines'}
          </button>
        </form>
        {rangeError && <p className="error" role="alert">{rangeError}</p>}
        <LineAiPicker picker={picker} disabled={busy} />
        <p data-testid="retranslate-selected-count" aria-live="polite">
          {selectedIds.length} line{selectedIds.length === 1 ? '' : 's'} ticked
          {estimate?.estimated_usd != null && ` · estimate ${formatUsd(estimate.estimated_usd + (estimate.alignment_check_usd ?? 0))}`}
        </p>
        <div className="review-actions">
          {confirming && !busy ? (
            <>
              <button type="button" onClick={start} data-testid="retranslate-selected-confirm">
                Replace English on {selectedIds.length} line{selectedIds.length === 1 ? '' : 's'}
              </button>
              <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setConfirming(false)}>Keep as is</button>
            </>
          ) : (
            <button type="button" onClick={() => setConfirming(true)} disabled={busy || !config || !!blocked}>
              {busy ? 'Re-translating…' : 'Re-translate selected…'}
            </button>
          )}
          {active && job && offersCancel(job, anyJob) && (
            <button type="button" className={buttonClass('secondary', 'sm')} title={cancelHint(job.job_id)} onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>
          )}
        </div>
        {blocked && !busy && <p className="muted" data-testid="retranslate-selected-blocked">{blocked}</p>}
        <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
        {active && job && <p className="muted" data-testid="retranslate-selected-progress">{job.message || 'Working…'}</p>}
        {job && !active && <p className="muted" data-testid="retranslate-selected-done">{job.status === 'done' ? 'Done. The lines are updated.' : job.message || `Run ${job.status}.`}</p>}
        {changes && !active && (
          <div data-testid="retranslate-diff">
            <p role="status">
              {changes.length === 0
                ? putBackCount > 0 ? 'All lines put back.' : 'The new translation matched the old one on every line.'
                : `${changes.length} line${changes.length === 1 ? '' : 's'} changed${putBackCount > 0 ? ` · ${putBackCount} put back` : ''}:`}
            </p>
            <ul className="retranslate-diff">
              {changes.slice(0, showAll ? undefined : 10).map((c) => (
                <li key={c.id}>
                  <div className="review-actions">
                    <strong>Line {c.number}</strong>
                    <button type="button" className={buttonClass('ghost', 'sm')} style={{ marginLeft: 'auto' }} disabled={pendingBack !== null} onClick={() => putBack(c)}>
                      {pendingBack === c.id ? 'Putting back…' : 'Put back'}
                    </button>
                  </div>
                  <div className="review-ai-compare">
                    <div><span className="muted">Before</span><div className="review-ai-text">{c.before || '(empty)'}</div></div>
                    <div><span className="muted">After</span><div className="review-ai-text">{c.after || '(empty)'}</div></div>
                  </div>
                </li>
              ))}
            </ul>
            {changes.length > 10 && !showAll && (
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setShowAll(true)}>Show all {changes.length}</button>
            )}
          </div>
        )}
      </div>
    </Section>
  )
}
