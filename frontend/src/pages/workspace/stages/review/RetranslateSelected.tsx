import { useEffect, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
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
import { useLineSelectionContext } from './LineSelectionContext'
import { formatUsd } from './strongerEngineLogic'

// Redoes the ticked lines with an engine and model picked here, as an ordinary
// Translate run limited to those lines: same glossary, style, characters, pronoun
// settings and context windows (taken from the title's saved run settings),
// with the lines around them as context. It replaces the ticked lines' English,
// hand edits included, so the button asks to confirm first; a line edited while
// the run works keeps the edit whatever the options.
export function RetranslateSelected({ dramaId, jobRunning, onChanged }: { dramaId: number; jobRunning: boolean; onChanged: () => void }) {
  const { selectedIds } = useLineSelectionContext()
  const picker = useLineAiPick(dramaId)
  const anyJob = useCancelsAnyJob()
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [estimate, setEstimate] = useState<TranslateRunEstimate | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const { job, error: pollError } = useJob(jobId, { runKey, onDone: () => onChanged() })

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
        <LineAiPicker picker={picker} disabled={busy} />
        <p data-testid="retranslate-selected-count">
          {selectedIds.length} line{selectedIds.length === 1 ? '' : 's'} ticked
          {estimate?.estimated_usd != null && ` · estimate ${formatUsd(estimate.estimated_usd)}`}
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
        {job && !active && <p className="muted" role="status" data-testid="retranslate-selected-done">{job.status === 'done' ? 'Done. The lines are updated.' : job.message || `Run ${job.status}.`}</p>}
      </div>
    </Section>
  )
}
