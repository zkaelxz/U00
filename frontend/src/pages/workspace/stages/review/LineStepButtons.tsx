import { buttonClass } from '../../../../components/uiClasses'

interface Props {
  flaggedCount: number
  unreviewedCount: number
  onMove: (delta: 1 | -1, kind?: 'flagged' | 'unreviewed') => void
}

// Previous/next flagged and "Next line to check"; a button shows only while it has somewhere to go.
export function LineStepButtons({ flaggedCount, unreviewedCount, onMove }: Props) {
  if (!flaggedCount && !unreviewedCount) return null
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
    </div>
  )
}
