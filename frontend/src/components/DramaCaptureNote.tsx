import { useState } from 'react'

import { getCaptureNotes, type CaptureNote } from '../api/library'
import { useCancellableEffect } from '../hooks/useCancellableEffect'
import { formatWhen } from '../pages/backupsFormat'

// The closing line of the latest extension capture saved into this title,
// kept so it outlives the extension popup. Older ones are one click away.
export function CaptureNoteView({ notes }: { notes: CaptureNote[] }) {
  const [showOlder, setShowOlder] = useState(false)
  if (notes.length === 0) return null
  const [latest, ...older] = notes
  const line = (n: CaptureNote) => (
    <>
      <p>{n.summary}</p>
      <span className="muted">{formatWhen(n.created_at) ?? ''}</span>
    </>
  )
  return (
    <div className="drama-capture-note" data-testid="drama-capture-note">
      <strong>Last capture</strong>
      {line(latest)}
      {older.length > 0 && (
        <button type="button" onClick={() => setShowOlder((v) => !v)} aria-expanded={showOlder}>
          Earlier captures ({older.length})
        </button>
      )}
      {showOlder && (
        <ul>
          {older.map((n) => <li key={n.created_at + n.summary}>{line(n)}</li>)}
        </ul>
      )}
    </div>
  )
}

export function DramaCaptureNote({ dramaId }: { dramaId: number }) {
  const [notes, setNotes] = useState<CaptureNote[]>([])
  useCancellableEffect((cancelled) => {
    getCaptureNotes(dramaId).then(
      (r) => !cancelled() && setNotes(r.items),
      () => {}, // a note is a nicety: the popup stays as it was without it
    )
  }, [dramaId])
  return <CaptureNoteView notes={notes} />
}
