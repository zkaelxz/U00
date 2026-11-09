import { useState, type FormEvent } from 'react'

import { buttonClass } from '../components/uiClasses'
import { MAX_URL_LEN } from './libraryForm'

/** One box at the top of the Library: paste a link and the New title sheet opens with it filled in. */
export function LibraryStartBox({ onStart }: { onStart: (link: string) => void }) {
  const [link, setLink] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = link.trim()
    if (!trimmed) return
    setLink('')
    onStart(trimmed)
  }
  return (
    <form className="library-start" onSubmit={submit} aria-label="Start from a link">
      <label htmlFor="library-start-link">Paste a link to start a title</label>
      <div className="library-start-row">
        <input
          id="library-start-link"
          type="url"
          inputMode="url"
          value={link}
          maxLength={MAX_URL_LEN}
          placeholder="https://"
          onChange={(e) => setLink(e.target.value)}
        />
        <button type="submit" className={buttonClass('secondary')} disabled={!link.trim()}>Start</button>
      </div>
    </form>
  )
}
