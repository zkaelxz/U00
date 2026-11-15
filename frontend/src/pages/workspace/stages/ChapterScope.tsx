// The Suggest terms bar's chapter choice: scan the whole title or one saved chapter.
import { useEffect, useState } from 'react'

import { getLineChapters } from '../../../api/novelChapters'
import type { NovelLineChapterRow } from '../../../types/novelChapters'

export function ChapterScope({ dramaId, value, onChange }: {
  dramaId: number
  value: number | null
  onChange: (chapter: number | null) => void
}) {
  const [chapters, setChapters] = useState<NovelLineChapterRow[]>([])
  useEffect(() => {
    let live = true
    getLineChapters(dramaId).then((r) => live && setChapters(r.chapters), () => {})
    return () => { live = false }
  }, [dramaId])
  // A title with no saved chapters has only the whole title to scan.
  if (chapters.length < 2) return null
  return (
    <select aria-label="Scan scope" value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">Whole title</option>
      {chapters.map((c) => (
        <option key={c.number} value={c.number}>{c.title ? `${c.number}. ${c.title}` : `Chapter ${c.number}`}</option>
      ))}
    </select>
  )
}
