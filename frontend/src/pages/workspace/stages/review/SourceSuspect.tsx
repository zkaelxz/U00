import { useCallback, useState } from 'react'

import { listAllLines, mergeLines } from '../../../../api/restructure'
import { checkSourceText } from '../../../../api/sourceSuspect'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { SourceSuspectItem } from '../../../../types/sourceSuspect'

interface PanelProps {
  dramaId: number
  items: SourceSuspectItem[]
  onGoTo: (n: number) => void
  onChanged: () => void
  onClose: () => void
}

// Each flagged line with why, and a "Merge with next" that is the ordinary
// merge (adjacent lines, refused if the list changed meanwhile). The check
// runs again afterwards because the merged line keeps the flag.
export function SourceSuspectPanel({ dramaId, items, onGoTo, onChanged, onClose }: PanelProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const merge = useCallback(
    async (item: SourceSuspectItem) => {
      if (item.next_line_id === null) return
      setBusy(true)
      try {
        const ids = (await listAllLines(dramaId)).map((l) => l.id)
        await mergeLines(dramaId, [item.line_id, item.next_line_id], ids)
        await checkSourceText(dramaId)
        setError(null)
        onChanged()
      } catch (e) {
        setError(e)
      } finally {
        setBusy(false)
      }
    },
    [dramaId, onChanged],
  )

  return (
    <section className="review-suspect" aria-label="Source text looks off">
      <div className="review-suspect-head">
        <strong>Source text looks off</strong>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      </div>
      <p className="muted">Fix the transcription before translating; a bad source line gives an odd translation.</p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ul>
        {items.map((it) => (
          <li key={it.line_id} data-testid="suspect-item">
            <span>
              <button type="button" className="review-suspect-line" onClick={() => onGoTo(lineNumber(it.idx))}>
                #{lineNumber(it.idx)}
              </button>{' '}
              {it.zh} <span className="muted">· {it.note}</span>
            </span>
            {it.next_line_id !== null && (
              <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => void merge(it)}>
                Merge with next{it.next_zh ? ` (“${it.next_zh}”)` : ''}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
