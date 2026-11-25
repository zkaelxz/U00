/** The Watch page's bookmarks: one tap saves the moment being played, with a note you may add or change; a tap on one plays from there. */
import { useCallback, useEffect, useState, type RefObject } from 'react'

import { ApiError } from '../../api/client'
import { readerApi } from '../../api/reader'
import { buttonClass } from '../../components/uiClasses'
import { formatClock } from '../../labels'
import type { WatchBookmark } from '../../types/reader'

const FAILED = 'The bookmark could not be saved. Try again in a moment.'

export function Bookmarks({ dramaId, mediaRef, canEdit, onPlay }: {
  dramaId: number
  mediaRef: RefObject<HTMLMediaElement | null>
  canEdit: boolean
  onPlay: (start: number) => void
}) {
  const [items, setItems] = useState<WatchBookmark[]>([])
  const [status, setStatus] = useState('')
  const [editing, setEditing] = useState<{ id: number; draft: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    readerApi.bookmarks(dramaId).then((r) => !cancelled && setItems(r.bookmarks), () => undefined) // bookmarks are optional
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const run = useCallback(async (work: () => Promise<void>) => {
    setStatus('')
    try {
      await work()
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 409 ? e.message : FAILED)
    }
  }, [])

  const add = () => run(async () => {
    const at = mediaRef.current?.currentTime ?? 0
    const saved = await readerApi.addBookmark(dramaId, at)
    setItems((all) => [...all, saved].sort((a, b) => a.at_seconds - b.at_seconds || a.id - b.id))
    setStatus(`Bookmarked ${formatClock(saved.at_seconds)}.`)
  })

  const saveNote = (id: number, note: string) => run(async () => {
    const saved = await readerApi.setBookmarkNote(dramaId, id, note)
    setItems((all) => all.map((b) => (b.id === id ? saved : b)))
    setEditing(null)
  })

  const remove = (id: number) => run(async () => {
    await readerApi.deleteBookmark(dramaId, id)
    setItems((all) => all.filter((b) => b.id !== id))
    setEditing(null)
  })

  if (!canEdit && items.length === 0) return null
  return (
    <section className="card" aria-labelledby="watch-bookmarks-title" data-testid="watch-bookmarks">
      <header className="watch-lines-head">
        <h2 className="card-title" id="watch-bookmarks-title">Bookmarks</h2>
        {canEdit && <button type="button" className={buttonClass()} onClick={() => void add()}>Bookmark this moment</button>}
      </header>
      {status && <p className="watch-now-status" role="status">{status}</p>}
      {items.length === 0 ? (
        <p className="muted">Nothing saved yet. Each account sees only its own.</p>
      ) : (
        <ul className="watch-bookmarks">
          {items.map((b) => (
            <li key={b.id}>
              <button type="button" className="watch-bookmark-play" onClick={() => onPlay(b.at_seconds)}>
                <span className="muted">{formatClock(b.at_seconds)}</span> {b.note || <span className="muted">No note</span>}
              </button>
              {canEdit && editing?.id !== b.id && (
                <span className="watch-bookmark-actions">
                  <button type="button" className={buttonClass()} onClick={() => setEditing({ id: b.id, draft: b.note })}>Note</button>
                  <button type="button" className={buttonClass()} aria-label={`Delete the bookmark at ${formatClock(b.at_seconds)}`} onClick={() => void remove(b.id)}>Delete</button>
                </span>
              )}
              {editing?.id === b.id && (
                <form className="watch-bookmark-edit" onSubmit={(e) => { e.preventDefault(); void saveNote(b.id, editing.draft) }}>
                  <input
                    type="text" maxLength={300} value={editing.draft} autoFocus aria-label="Note"
                    onChange={(e) => setEditing({ id: b.id, draft: e.target.value })}
                    onKeyDown={(e) => { if (e.key === 'Escape') setEditing(null) }}
                  />
                  <button type="submit" className={buttonClass()}>Save</button>
                  <button type="button" className={buttonClass()} onClick={() => setEditing(null)}>Cancel</button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
