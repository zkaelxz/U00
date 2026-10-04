import { useState } from 'react'

import { flagAutoQc, flagDenseLines, flagOverlaps } from '../../../../api/export'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import { Section } from '../../../../components/Section'
import { useStage } from '../../StageContext'

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
    <Section storageKey="review.flags" title="Flag lines for review" summary="Overlaps, auto-QC, dense lines">
      <div className="review-flags" aria-label="Flag lines">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {ACTIONS.map((a) => (
        <div className="review-flag-row" key={a.id}>
          <button type="button" className={buttonClass('secondary')} disabled={busy !== null} onClick={() => run(a)}>{a.label}</button>
          <span className="muted">{a.writes}</span>
          {results[a.id] && <span role="status" data-testid={`flag-result-${a.id}`}>{results[a.id]}</span>}
        </div>
      ))}
      </div>
    </Section>
  )
}
