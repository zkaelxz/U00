import { useEffect, useState } from 'react'

import { getResearchProvenance } from '../../../api/research'
import { provenanceNotes } from '../researchForm'
import type { ProvenanceNote as Note } from '../researchForm'

export function ProvenanceNotes({ notes }: { notes: Note[] }) {
  if (!notes.length) return null
  return (
    <details className="research-provenance">
      <summary>Where saved details came from ({notes.length})</summary>
      <ul aria-label="Saved sources">
        {notes.map((n) => (
          <li key={n.field}><strong>{n.label}:</strong> {n.text}</li>
        ))}
      </ul>
    </details>
  )
}

// Read-only. Loading, an empty list and a failed read all render nothing so
// the research controls are never blocked by this note.
export function ProvenanceNote({ dramaId, reloadKey }: { dramaId: number; reloadKey?: unknown }) {
  const [notes, setNotes] = useState<Note[]>([])
  useEffect(() => {
    let live = true
    getResearchProvenance(dramaId).then(
      (p) => { if (live) setNotes(provenanceNotes(p.fields)) },
      () => { if (live) setNotes([]) },
    )
    return () => { live = false }
  }, [dramaId, reloadKey])
  return <ProvenanceNotes notes={notes} />
}
