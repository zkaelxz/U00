// The "better wording" box under a line marked wrong: the suggestion teaches the learned style without opening the editor.
import { useState } from 'react'

import { ApiError } from '../../api/client'
import { sendLineFeedback } from '../../api/review'
import { buttonClass } from '../../components/uiClasses'
import type { ReviewLine } from '../../types/review'

export function LineFeedback({ id, line, onSaved }: { id: number; line: ReviewLine; onSaved: (line: ReviewLine) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')

  const send = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      onSaved(await sendLineFeedback(id, line.id, text, line.en))
      setStatus('Thanks. Your wording is kept for the learned style.')
      setOpen(false)
      setText('')
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 409 ? 'This line changed. Check it, then try again.' : 'Could not send it. Try again in a moment.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="watch-feedback">
      {open ? (
        <>
          <label className="watch-feedback-label">
            Better wording (optional)
            <textarea value={text} rows={2} maxLength={2000} onChange={(e) => setText(e.target.value)} />
          </label>
          <button type="button" className={buttonClass()} disabled={busy || !text.trim()} onClick={() => void send()}>Send</button>
          <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
        </>
      ) : (
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setOpen(true)}>Suggest better wording</button>
      )}
      <span role="status">{status}</span>
    </div>
  )
}
