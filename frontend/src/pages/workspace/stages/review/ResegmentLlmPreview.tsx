import { useState } from 'react'

import { TypedConfirm } from '../../../../components/TypedConfirm'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ResegmentLlmPreview as Preview } from '../../../../types/restructure'
import { RESEGMENT_COST_RECORDED, droppedText, llmPreviewSummary } from './reviewResegment'
import './resegment.css'

const SHOWN_CHANGES = 8

interface Props {
  preview: Preview
  // The preview asked for confirmation, or the server refused without it.
  needsConfirm: boolean
  // A reason Apply cannot run now (a job on the drama), else null.
  blocked: string | null
  busy: boolean
  onApply: (confirm: boolean) => void
  onDiscard: () => void
}

// The AI's proposed splits (nothing is written yet): each changed line with
// its pieces, the line count before and after, then Apply or Discard.
export function ResegmentLlmPreview({ preview, needsConfirm, blocked, busy, onApply, onDiscard }: Props) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? preview.changed : preview.changed.slice(0, SHOWN_CHANGES)
  const more = preview.changed.length - SHOWN_CHANGES
  const discard = (
    <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={onDiscard}>
      Discard
    </button>
  )
  return (
    <div className="stack reseg-ai" data-testid="resegment-ai-preview" aria-label="AI re-segmentation preview" role="group">
      <p className="reseg-ai-summary">{llmPreviewSummary(preview)}</p>
      {preview.changed.length === 0 ? (
        <>
          <p className="muted">The AI found nothing to split.</p>
          <p className="muted">{RESEGMENT_COST_RECORDED}</p>
          <div className="actions">{discard}</div>
        </>
      ) : (
        <>
          <ol className="reseg-ai-list" aria-label="Proposed splits">
            {shown.map((c) => (
              <li key={`${c.line_id ?? 'x'}-${c.idx}`} className="reseg-ai-item">
                <div className="reseg-ai-from">
                  <span className="muted">#{lineNumber(c.idx)}</span> <span lang="zh">{c.zh}</span>
                </div>
                <ol className="reseg-ai-pieces" aria-label={`Line ${lineNumber(c.idx)} becomes ${c.pieces.length} lines`}>
                  {c.pieces.map((piece, i) => (
                    <li key={i} lang="zh">
                      {piece}
                    </li>
                  ))}
                </ol>
              </li>
            ))}
          </ol>
          {more > 0 && (
            <div className="actions">
              <button type="button" className={buttonClass('ghost')} aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show fewer' : `and ${more} more — show all`}
              </button>
            </div>
          )}
          <p className="muted">{RESEGMENT_COST_RECORDED}</p>
          {needsConfirm ? (
            <>
              <p className="reseg-ai-warn" role="note">
                {droppedText(preview)}
              </p>
              <TypedConfirm word="resegment" action="Apply" blocked={blocked} busy={busy} onConfirm={() => onApply(true)}>
                <p className="muted">Your current lines are saved as a snapshot first (Records → Line history).</p>
              </TypedConfirm>
              <div className="actions">{discard}</div>
            </>
          ) : (
            <>
              <p className="muted">Your current lines are saved as a snapshot first (Records → Line history).</p>
              {blocked && <p className="muted">{blocked}</p>}
              <div className="actions">
                <button type="button" className={buttonClass('primary')} disabled={busy || !!blocked} onClick={() => onApply(false)}>
                  {busy ? 'Working…' : 'Apply'}
                </button>
                {discard}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
