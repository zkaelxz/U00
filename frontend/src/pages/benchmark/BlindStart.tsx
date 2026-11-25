import { useState } from 'react'

import { getBenchmarkPairSuggestion } from '../../api/benchmark'
import { buttonClass } from '../../components/uiClasses'
import { plainError } from './benchmarkForm'
import { blindStartText } from './blindPickText'

/** One tap into a blind comparison: the app picks the two runs, so nothing has to be chosen first. */
export function BlindStart({ onStart }: { onStart: (runIds: number[]) => void }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const start = async () => {
    setBusy(true)
    setNote(null)
    try {
      const s = await getBenchmarkPairSuggestion()
      if (s.found && s.run_a != null && s.run_b != null) {
        setNote(blindStartText(s))
        onStart([s.run_a, s.run_b])
      } else {
        setNote(blindStartText(s))
      }
    } catch (e) {
      setNote(plainError(e))
    }
    setBusy(false)
  }

  return (
    <p className="status-line" data-testid="blind-start">
      <button type="button" className={buttonClass('secondary', 'sm')} onClick={start} disabled={busy}>
        Start blind comparison
      </button>
      {note && <span role="status">{note}</span>}
    </p>
  )
}
