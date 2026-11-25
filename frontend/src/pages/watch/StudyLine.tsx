// A small "Study" button on the Watch page's now card: the line's original text, then each word with its reading and
// meaning. It is a button, not a tap on the line, because tapping a line jumps the video there.
import { useEffect, useState } from 'react'

import { readerApi } from '../../api/reader'
import { buttonClass } from '../../components/uiClasses'
import type { StudyLine as Study } from '../../types/reader'

export function StudyLine({ dramaId, lineId, language }: { dramaId: number; lineId: number; language: string }) {
  const [open, setOpen] = useState(false)
  const [study, setStudy] = useState<Study | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!open || study) return
    let cancelled = false
    readerApi.lineStudy(dramaId, lineId).then(
      (s) => !cancelled && setStudy(s),
      () => !cancelled && setFailed(true),
    )
    return () => {
      cancelled = true
    }
  }, [open, study, dramaId, lineId])

  return (
    <div className="watch-study">
      <button type="button" className={buttonClass()} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide study' : 'Study'}
      </button>
      {open && failed && <p role="status">The breakdown could not be loaded.</p>}
      {open && !failed && !study && <p role="status">Loading…</p>}
      {open && study && <StudyWords study={study} language={language} />}
    </div>
  )
}

export function StudyWords({ study, language }: { study: Study; language: string }) {
  return (
    <div data-testid="watch-study">
      {study.needs_install && <p role="status">Breaking this into words needs {study.needs_install}, which is not installed (see Diagnostics).</p>}
      {study.words.length === 0 && !study.needs_install && <p role="status">Nothing to break down for this line.</p>}
      <ul className="watch-study-words" lang={study.language || language}>
        {study.words.map((w, i) => (
          <li key={i} className="watch-study-word">
            <span>{w.word}</span>
            {w.reading && <span className="watch-study-reading">{w.reading}</span>}
            {w.meanings && w.meanings.length > 0 && <span className="watch-study-meaning" lang="en">{w.meanings.join('; ')}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}
