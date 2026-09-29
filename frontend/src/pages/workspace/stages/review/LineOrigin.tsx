import { useState } from 'react'

import { getLineOriginalText, getLineProvenance } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { safeDetail } from '../../../../components/errorMessages'
import type { LineOriginalText, LineProvenance } from '../../../../types/review'
import { emotionText, glossaryText, termsText } from './reviewResults'

// "Where this line came from", folded inside the line's details: the original
// transcription and what the app knows about how the translation was made.
// Fetched each time it is opened, so it never shows data from before a save.
export function LineOrigin({ dramaId, lineId }: { dramaId: number; lineId: number }) {
  const [data, setData] = useState<{ p: LineProvenance; o: LineOriginalText } | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = () => {
    setData(null)
    Promise.all([getLineProvenance(dramaId, lineId), getLineOriginalText(dramaId, lineId)]).then(
      ([p, o]) => {
        setError(null)
        setData({ p, o })
      },
      setError,
    )
  }

  return (
    <details
      className="review-origin"
      data-testid="line-origin"
      onToggle={(e) => {
        if (e.currentTarget.open) load()
      }}
    >
      <summary>Where this line came from</summary>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {data ? <OriginBody p={data.p} o={data.o} /> : !error && <p className="muted">Loading…</p>}
    </details>
  )
}

function OriginBody({ p, o }: { p: LineProvenance; o: LineOriginalText }) {
  const glossary = glossaryText(p.glossary_matches)
  const issues = termsText(p.consistency_issues)
  const notes = termsText(p.translation_notes)
  const emotion = emotionText(p.emotion)
  const source = p.engine_source ? safeDetail(p.engine_source) : null
  const rows: [string, string][] = []
  if (p.engine) rows.push(['Engine', [p.engine, p.model].filter(Boolean).join(' · ')])
  if (p.flag_reason) rows.push(['Flag', [p.flag_reason, p.flag_note].filter(Boolean).join(' · ')])
  if (emotion) rows.push(['Emotion', emotion])
  if (glossary.length) rows.push(['Glossary', glossary.join(', ')])
  if (issues.length) rows.push(['Consistency', issues.join(', ')])
  if (notes.length) rows.push(['Notes', notes.join(', ')])
  if (p.edit_samples.length) rows.push(['Your edits', `${p.edit_samples.length} recorded for this source text`])

  return (
    <div className="review-origin-body">
      {o.has_raw_transcript && (
        <p data-testid="original-text">
          {o.original_text === null ? (
            <span className="muted">No original text at this time.</span>
          ) : o.differs ? (
            <>
              Originally transcribed as <span lang="zh">“{o.original_text}”</span>
            </>
          ) : (
            <span className="muted">Same as the original transcription.</span>
          )}
        </p>
      )}
      {rows.length > 0 && (
        <dl className="review-origin-list">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {source && <p className="muted">Engine from {source}.</p>}
    </div>
  )
}
