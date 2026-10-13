import { useEffect, useState } from 'react'

import { withSignal } from '../../../api/client'
import { getAsrAdvice } from '../../../api/workspace'
import { buttonClass } from '../../../components/uiClasses'
import type { AsrAdviceNote, AsrAdviceRequest } from '../../../types/workspace'

const ADVICE_DEBOUNCE_MS = 250

/** The form's choices as the advisor takes them; one object so a change in any of them re-asks. */
export function adviceKey(req: AsrAdviceRequest): string {
  return JSON.stringify(req)
}

/**
 * One muted line under the model picker: the server's top note for the choices on the form, with a
 * button only when the fix is a plain setting change. Silent while loading or when the read fails:
 * the advice is never a reason to block the form.
 */
export function AsrAdviceLine({ request, onApply }: { request: AsrAdviceRequest; onApply: (fix: Record<string, string | boolean>) => void }) {
  const [note, setNote] = useState<AsrAdviceNote | null>(null)
  const key = adviceKey(request)
  useEffect(() => {
    const ctl = new AbortController()
    const timer = window.setTimeout(() => {
      getAsrAdvice(JSON.parse(key) as AsrAdviceRequest, withSignal(ctl.signal))
        .then((notes) => setNote(notes[0] ?? null))
        .catch(() => { if (!ctl.signal.aborted) setNote(null) })
    }, ADVICE_DEBOUNCE_MS)
    return () => { window.clearTimeout(timer); ctl.abort() }
  }, [key])
  return note && <AsrAdviceView note={note} onApply={onApply} />
}

/** The note as one muted line; the button shows only for a fix that is a plain setting change. */
export function AsrAdviceView({ note, onApply }: { note: AsrAdviceNote; onApply: (fix: Record<string, string | boolean>) => void }) {
  return (
    <p className="muted" role="note" data-testid="transcribe-advisor" data-severity={note.severity}>
      {note.text}
      {note.fix && (
        <>
          {' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onApply(note.fix!)}>
            Use recommended
          </button>
        </>
      )}
    </p>
  )
}
