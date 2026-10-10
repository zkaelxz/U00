import { useEffect, useState } from 'react'

import {
  clearReadingSpeedFlags, flagAutoQc, flagDenseLines, flagOverlaps, getReadingSpeedMode, setReadingSpeedMode,
} from '../../../../api/export'
import type { ReadingSpeedMode } from '../../../../types/export'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import { Section } from '../../../../components/Section'
import { useStage } from '../../StageContext'
import { clearMessage, MODE_HELP, MODE_OPTIONS } from './readingSpeed'
import { TimingCheck } from './TimingCheck'

interface Action {
  id: string
  label: string
  writes: string
  run: (id: number) => Promise<string>
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

const ACTIONS: Action[] = [
  {
    id: 'overlaps',
    label: 'Flag overlapping lines',
    writes: 'Marks each line that overlaps the next one, and is not flagged yet, for review. Only flags and flag notes are written.',
    run: (id) => flagOverlaps(id).then((r) => `Flagged ${plural(r.flagged_count, 'line')}.`),
  },
  {
    id: 'qc',
    label: 'Run auto-QC and flag',
    writes: 'Checks names, numbers and other factual details, flags lines that differ, and clears flags it set earlier that no longer apply. Only flags and flag notes are written.',
    run: (id) =>
      flagAutoQc(id).then(
        (r) =>
          `Checked ${plural(r.checked, 'line')}: flagged ${r.flagged}, cleared ${r.cleared}, already flagged ${r.already_flagged}.`,
      ),
  },
  {
    id: 'dense',
    label: 'Flag dense lines',
    writes: 'Flags lines with too much text to read in their on-screen time. Only flags and flag notes are written.',
    run: (id) => flagDenseLines(id).then((r) => `Flagged ${plural(r.flagged_count, 'line')}.`),
  },
]

// Writes only flags and flag notes; the same endpoints Export used to host.
export function ReviewFlags({ onDone }: { onDone: () => void }) {
  const { dramaId } = useStage()
  const [busy, setBusy] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, string>>({})
  const [error, setError] = useState<unknown>(null)
  const [mode, setMode] = useState<ReadingSpeedMode | null>(null)
  const [confirming, setConfirming] = useState<'clear' | 'recheck' | null>(null)
  const [clearResult, setClearResult] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getReadingSpeedMode(dramaId).then((r) => live && setMode(r.mode), (e: unknown) => live && setError(e))
    return () => {
      live = false
    }
  }, [dramaId])

  const changeMode = (next: ReadingSpeedMode) => {
    setBusy('mode')
    setReadingSpeedMode(dramaId, next).then(
      (r) => {
        setError(null)
        setMode(r.mode)
        setBusy(null)
      },
      (e: unknown) => {
        setError(e)
        setBusy(null)
      },
    )
  }

  const clearFlags = (recheck: boolean) => {
    setConfirming(null)
    setBusy('clear')
    clearReadingSpeedFlags(dramaId, recheck).then(
      (r) => {
        setError(null)
        setClearResult(clearMessage(r, recheck))
        setBusy(null)
        onDone()
      },
      (e: unknown) => {
        setError(e)
        setBusy(null)
      },
    )
  }

  const run = (a: Action) => {
    setBusy(a.id)
    a.run(dramaId).then(
      (msg) => {
        setError(null)
        setResults((r) => ({ ...r, [a.id]: msg }))
        setBusy(null)
        onDone()
      },
      (e: unknown) => {
        setError(e)
        setBusy(null)
      },
    )
  }

  return (
    <Section storageKey="review.flags" title="Flag lines for review" summary="Overlaps, auto-QC, dense lines, timing, reading speed">
      <div className="review-flags" aria-label="Flag lines">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {ACTIONS.map((a) => (
        <div className="review-flag-row" key={a.id}>
          <button type="button" className={buttonClass('secondary')} disabled={busy !== null} onClick={() => run(a)}>{a.label}</button>
          <span className="muted">{a.writes}</span>
          {results[a.id] && <span role="status" data-testid={`flag-result-${a.id}`}>{results[a.id]}</span>}
        </div>
      ))}
      <TimingCheck dramaId={dramaId} onDone={onDone} />
      <div className="review-flag-row">
        <label>
          Reading speed check{' '}
          <select value={mode ?? 'normal'} disabled={mode === null || busy !== null}
                  onChange={(e) => changeMode(e.target.value as ReadingSpeedMode)}>
            {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <span className="muted">{MODE_HELP}</span>
      </div>
      <div className="review-flag-row">
        {confirming === null ? (
          <>
            <button type="button" className={buttonClass('secondary')} disabled={busy !== null}
                    onClick={() => setConfirming('clear')}>Clear reading-speed flags</button>
            <button type="button" className={buttonClass('secondary')} disabled={busy !== null}
                    onClick={() => setConfirming('recheck')}>Re-check with the current setting</button>
            <span className="muted">Removes only the reading-speed flag from every line; other flags, notes and text stay. Saves a history snapshot first.</span>
          </>
        ) : (
          <>
            <span role="alert">
              {confirming === 'clear'
                ? 'Clear every reading-speed flag on this title?'
                : 'Clear every reading-speed flag, then flag lines again with the current setting?'}
            </span>
            <button type="button" className={buttonClass('primary')} onClick={() => clearFlags(confirming === 'recheck')}>Confirm</button>
            <button type="button" className={buttonClass('ghost')} onClick={() => setConfirming(null)}>Cancel</button>
          </>
        )}
        {clearResult && <span role="status" data-testid="flag-result-clear">{clearResult}</span>}
      </div>
      </div>
    </Section>
  )
}
