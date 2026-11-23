/** The Wrong and Partly buttons (a symbol and its word) and the tag that shows your mark on a line; a tick is the Checked button that already exists. */
import { Badge } from '../../../../components/Badge'
import { buttonClass } from '../../../../components/uiClasses'
import type { LineMark, ReviewLine } from '../../../../types/review'

export const MARK_LABEL: Record<LineMark, string> = { wrong: 'Wrong', mixed: 'Partly' }
const MARK_GLYPH: Record<LineMark, string> = { wrong: '✗', mixed: '〰' }
const MARK_HELP: Record<LineMark, string> = {
  wrong: 'Mark this English wrong. It stays marked until you clear it, check the line or change the English.',
  mixed: 'Mark this English as partly right. It stays marked until you clear it, check the line or change the English.',
}

// Pressing the mark a line already has clears it. A line with no English has nothing to judge.
export function LineMarkButtons({ line, onMark, variant = 'secondary' }: {
  line: ReviewLine
  onMark: (id: number, mark: LineMark | null) => void
  // Follows the toolbar the buttons sit in.
  variant?: 'secondary' | 'ghost'
}) {
  return (
    <>
      {(['wrong', 'mixed'] as const).map((m) => (
        <button key={m} type="button" className={buttonClass(variant, 'sm', `review-mark review-mark-${m}`)} disabled={!line.en} aria-pressed={line.mark === m} aria-label={MARK_LABEL[m]}
                title={line.mark === m ? `Clear the ${m} mark` : MARK_HELP[m]} onClick={() => onMark(line.id, line.mark === m ? null : m)}>
          <span aria-hidden="true">{MARK_GLYPH[m]}</span> {MARK_LABEL[m]}
        </button>
      ))}
    </>
  )
}

export function LineMarkBadge({ line }: { line: Pick<ReviewLine, 'mark'> }) {
  const m = line.mark === 'wrong' || line.mark === 'mixed' ? line.mark : null
  if (!m) return null
  return (
    <span data-testid="line-mark">
      <Badge tone={m === 'wrong' ? 'bad' : 'warn'}><span aria-hidden="true">{MARK_GLYPH[m]}</span> {MARK_LABEL[m]}</Badge>
    </span>
  )
}
