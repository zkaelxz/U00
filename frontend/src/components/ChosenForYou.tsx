/*
 * Engine and model picked for the viewer (owner decision 2026-11-24): a member's always, and anyone's on another
 * device until an admin there presses Show options. The page hides its pickers while `auto` is true and sends
 * no engine, so the server chooses one the account may use.
 */
import { useState } from 'react'

import { usePcOnly } from '../hooks/usePcOnly'
import { pickedForYou, useSession } from '../hooks/useSession'
import { buttonClass } from './uiClasses'

export function useChosenForYou() {
  const picked = pickedForYou(useSession(), usePcOnly())
  const [showOptions, setShowOptions] = useState(false)
  return { auto: picked === 'member' || (picked === 'away' && !showOptions), canShow: picked === 'away', show: () => setShowOptions(true) }
}

export function ChosenForYouNote({ text, chosen, testId }: { text: string; chosen: ReturnType<typeof useChosenForYou>; testId: string }) {
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
