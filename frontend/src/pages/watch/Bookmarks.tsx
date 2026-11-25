/** The Watch page's bookmarks: one tap saves the moment being played, with a note you may add or change; a tap on one plays from there. */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { ApiError } from '../../api/client'
import { readerApi } from '../../api/reader'
import { buttonClass } from '../../components/uiClasses'
import { formatClock } from '../../labels'
import type { WatchBookmark } from '../../types/reader'
import { withBookmark } from './bookmarkList'

const FAILED = 'The bookmark could not be saved. Try again in a moment.'
// Long enough to notice a slip and tap Undo, short enough that the line does not linger.
const UNDO_MS = 8000

export function Bookmarks({ dramaId, mediaRef, canEdit, onPlay }: {
  dramaId: number
  mediaRef: RefObject<HTMLMediaElement | null>
  canEdit: boolean
  onPlay: (start: number) => void
}) {
  const [items, setItems] = useState<WatchBookmark[]>([])
  const [status, setStatus] = useState('')
  const [editing, setEditing] = useState<{ id: number; draft: string } | null>(null)
  // Delete is one tap, so the last deleted bookmark (with its note) can be put back for a few seconds.
  const [deletedIn, setDeleted] = useState<{ dramaId: number; bookmark: WatchBookmark } | null>(null)
  // Another title's offer must not restore into this one if the page switches titles without remounting.
  const deleted = deletedIn?.dramaId === dramaId ? deletedIn.bookmark : null
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearUndo = useCallback(() => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    undoTimer.current = null
    setDeleted(null)
  }, [])
  const offerUndo = useCallback((b: WatchBookmark) => {
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setDeleted({ dramaId, bookmark: b })
    undoTimer.current = setTimeout(clearUndo, UNDO_MS)
  }, [clearUndo, dramaId])
  useEffect(() => () => { if (undoTimer.current) clearTimeout(undoTimer.current) }, [])

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
    setItems((all) => withBookmark(all, saved))
    setStatus(`Bookmarked ${formatClock(saved.at_seconds)}.`)
  })

  const saveNote = (id: number, note: string) => run(async () => {
    const saved = await readerApi.setBookmarkNote(dramaId, id, note)
    setItems((all) => all.map((b) => (b.id === id ? saved : b)))
    setEditing(null)
  })

  const remove = (b: WatchBookmark) => run(async () => {
    await readerApi.deleteBookmark(dramaId, b.id)
    setItems((all) => all.filter((x) => x.id !== b.id))
    setEditing(null)
    offerUndo(b)
    setStatus(`Bookmark at ${formatClock(b.at_seconds)} deleted.`)
  })

  const undoDelete = () => {
    if (!deleted) return
    const gone = deleted
    // Taken out before the request so a second tap cannot restore it twice; a failure offers it again.
    clearUndo()
    void run(async () => {
      try {
        const saved = await readerApi.addBookmark(dramaId, gone.at_seconds, gone.note)
        setItems((all) => withBookmark(all, saved))
        setStatus(`Bookmark at ${formatClock(saved.at_seconds)} restored.`)
      } catch (e) {
        offerUndo(gone)
        throw e
      }
    })
  }

  if (!canEdit && items.length === 0) return null
  return (
    <section className="card" aria-labelledby="watch-bookmarks-title" data-testid="watch-bookmarks">
      <header className="watch-lines-head">
        <h2 className="card-title" id="watch-bookmarks-title">Bookmarks</h2>
        {canEdit && <button type="button" className={buttonClass()} onClick={() => void add()}>Bookmark this moment</button>}
      </header>
      <div className="watch-bookmark-status">
        <p className="watch-now-status" role="status">{status}</p>
        {deleted && (
          <button type="button" className={buttonClass()} aria-label={`Undo deleting the bookmark at ${formatClock(deleted.at_seconds)}`} onClick={undoDelete}>Undo</button>
        )}
      </div>
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
                  <button type="button" className={buttonClass()} aria-label={`Delete the bookmark at ${formatClock(b.at_seconds)}`} onClick={() => void remove(b)}>Delete</button>
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
