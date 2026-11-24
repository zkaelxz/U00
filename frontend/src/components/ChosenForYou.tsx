/* The line a page shows in place of its engine pickers while they are chosen for the viewer (hooks/useChosenForYou). */
import type { ChosenForYou } from '../hooks/useChosenForYou'
import { buttonClass } from './uiClasses'

export function ChosenForYouNote({ text, chosen, testId }: { text: string; chosen: ChosenForYou; testId: string }) {
  return (
    <p className="muted translate-engine-note" data-testid={testId}>
      {text}{' '}
      {chosen.canShow && (
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={chosen.show}>
          Show options
        </button>
      )}
    </p>
  )
}
