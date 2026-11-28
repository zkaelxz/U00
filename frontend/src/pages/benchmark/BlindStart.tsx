import { useState } from 'react'

import { getBenchmarkPairSuggestion } from '../../api/benchmark'
import { buttonClass } from '../../components/uiClasses'
import { plainError } from './benchmarkForm'
import { blindStartText } from './blindPickText'

/** One tap into a blind comparison: the app picks the two runs, so nothing has to be chosen first. `onNote` gets what it found. */
export function BlindStart({ onStart, onNote }: { onStart: (runIds: number[]) => void; onNote: (note: string | null) => void }) {
  const [busy, setBusy] = useState(false)

  const start = async () => {
    setBusy(true)
    onNote(null)
    try {
      const s = await getBenchmarkPairSuggestion()
      if (s.found && s.run_a != null && s.run_b != null) {
        onNote(blindStartText(s))
        onStart([s.run_a, s.run_b])
      } else {
        onNote(blindStartText(s))
      }
    } catch (e) {
      onNote(plainError(e))
    }
    setBusy(false)
  }

  return (
    <button type="button" className={buttonClass('secondary', 'sm')} onClick={start} disabled={busy}>
      Start blind comparison
    </button>
  )
}
