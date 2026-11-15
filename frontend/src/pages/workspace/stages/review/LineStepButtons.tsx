import { buttonClass } from '../../../../components/uiClasses'
import type { StepKind } from '../../../../types/review'

interface Props {
  flaggedCount: number
  unreviewedCount: number
  noSpeakerCount?: number
  onMove: (delta: 1 | -1, kind?: StepKind) => void
}

// Previous/next flagged, "Next line to check" and "Next with no speaker"; a button shows only while it has somewhere to go.
export function LineStepButtons({ flaggedCount, unreviewedCount, noSpeakerCount = 0, onMove }: Props) {
  if (!flaggedCount && !unreviewedCount && !noSpeakerCount) return null
  return (
    <div className="review-flagnav" role="group" aria-label="Move between lines">
      {!!flaggedCount && (
        <>
          <button type="button" className={buttonClass('secondary', 'sm')} title="Previous flagged line (Alt+↑)" onClick={() => onMove(-1)}>
            ‹ Previous flagged
          </button>
          <button type="button" className={buttonClass('secondary', 'sm')} title="Next flagged line (Alt+↓)" onClick={() => onMove(1)}>
            Next flagged ›
          </button>
        </>
      )}
      {!!unreviewedCount && (
        <button type="button" className={buttonClass('secondary', 'sm')} title="Next translated line nobody has marked checked" onClick={() => onMove(1, 'unreviewed')}>
          Next line to check ›
        </button>
      )}
      {!!noSpeakerCount && (
        <button type="button" className={buttonClass('secondary', 'sm')} title="Next line with no speaker (N; Shift+N goes back)" onClick={() => onMove(1, 'no_speaker')}>
          Next with no speaker ({noSpeakerCount}) ›
        </button>
      )}
    </div>
  )
}
