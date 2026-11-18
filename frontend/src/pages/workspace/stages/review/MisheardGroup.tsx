import { useState } from 'react'

import { applyMisheard, keepMisheard, showKeptMisheard } from '../../../../api/misheard'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { MisheardSuggestion } from '../../../../types/misheard'

interface GroupProps {
  dramaId: number
  items: MisheardSuggestion[]
  kept?: number
  onGoTo: (n: number) => void
  onChanged: () => void
}

// The heard spelling underlined inside the line, so the part in question is easy to find.
function marked(text: string, heard: string) {
  const at = text.indexOf(heard)
  if (at < 0) return text
  return (
    <>
      {text.slice(0, at)}
      <u>{heard}</u>
      {text.slice(at + heard.length)}
    </>
  )
}

// Nothing here changes a line until a button is pressed; each press is one History step.
export function MisheardGroup({ dramaId, items, kept = 0, onGoTo, onChanged }: GroupProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const run = async (call: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await call()
      setError(null)
      onChanged()
    } catch (e) {
      setError(e)
      onChanged() // a refused line is stale: reload so the list shows what is there now
    } finally {
      setBusy(false)
    }
  }
  const apply = (s: MisheardSuggestion, line?: { line_id: number; zh: string }) =>
    run(() => applyMisheard(dramaId, {
      heard: s.heard,
      suggested: s.suggested,
      ...(line ? { line_id: line.line_id, expected_zh: line.zh } : {}),
    }))

  return (
    <div className="review-misheard" role="group" aria-label="May be mis-heard">
      <strong>May be mis-heard</strong>
      <p className="muted">
        These sound like a word the title already uses, spelled another way. Check by ear before using one;
        Use here and Use everywhere change the source text only, and a translated line is then flagged Source changed. Keep as heard hides a suggestion for this title.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ul>
        {items.map((s) => (
          <li key={`${s.heard}>${s.suggested}`} data-testid="misheard-item">
            <span>
              Maybe <strong>{s.suggested}</strong>? Heard as <u>{s.heard}</u> on {s.line_count}{' '}
              {s.line_count === 1 ? 'line' : 'lines'}; the suggested spelling appears {s.suggested_count}{' '}
              {s.suggested_count === 1 ? 'time' : 'times'}.
            </span>
            {s.lines.map((l) =>
              l.line_id == null ? null : (
                <span key={l.line_id} className="review-misheard-line">
                  <button type="button" className="review-suspect-line" onClick={() => onGoTo(lineNumber(l.idx))}>
                    #{lineNumber(l.idx)}
                  </button>{' '}
                  {marked(l.zh, s.heard)}
                  <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy}
                          onClick={() => void apply(s, { line_id: l.line_id as number, zh: l.zh })}>
                    Use here
                  </button>
                </span>
              ),
            )}
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy}
                    onClick={() => void run(() => keepMisheard(dramaId, { heard: s.heard, suggested: s.suggested }))}>
              Keep as heard
            </button>
            {s.line_count > 1 && (
              <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => void apply(s)}>
                Use everywhere ({s.line_count} lines)
              </button>
            )}
          </li>
        ))}
      </ul>
      {kept > 0 && (
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy}
                onClick={() => void run(() => showKeptMisheard(dramaId))}>
          Show {kept} kept {kept === 1 ? 'word' : 'words'} again
        </button>
      )}
    </div>
  )
}
