import { useState } from 'react'

import { addHardSubCases } from '../../../../api/benchmark'
import { describeError } from '../../../../components/errorMessages'
import { usePcOnly } from '../../../../hooks/usePcOnly'
import type { ReviewLine } from '../../../../types/review'

// "Add a hard-sub test case" in the line sheet. Cuts pictures of this line's burned-in subtitle band from the
// title's video as a Benchmark Lab OCR case. PC only (the route is local_only, the video is read there), hidden elsewhere.
export function HardSubCaseButton({ dramaId, line }: { dramaId: number; line: ReviewLine }) {
  const pc = usePcOnly()
  const [busy, setBusy] = useState(false)
  const [added, setAdded] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  if (pc === 'remote') return null
  const add = () => {
    setBusy(true)
    setNote(null)
    addHardSubCases(dramaId, [line.id]).then(
      () => setAdded(true),
      (e: unknown) => setNote(describeError(e, { pcOnly: true }).title),
    ).finally(() => setBusy(false))
  }
  return (
    <>
      <button type="button" disabled={busy || added || !line.zh} onClick={add}>
        {busy ? 'Adding…' : 'Add a hard-sub test case'}
      </button>
      {note && <p className="muted" role="status">{note}</p>}
    </>
  )
}
