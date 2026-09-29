import { useEffect, useState } from 'react'

import { api, ApiError } from '../api/client'
import { deleteDrama } from '../api/library'
import type { DramaDetail } from '../api/types'
import type { DramaDeleteResult } from '../types/library'
import { isComicType } from '../pages/comic/comicLogic'
import { canConfirmDelete } from '../pages/libraryForm'
import { routeHref } from '../router'
import { ErrorBanner } from './ErrorBanner'

function credit(name: string | null, romanized: string | null) {
  if (!name) return null
  return romanized && romanized !== name ? `${name} (${romanized})` : name
}

interface Props {
  dramaId: number
  // When given, the panel offers a typed-confirmation delete.
  onDeleted?: (result: DramaDeleteResult) => void
  // Shown where the delete button would be when onDeleted is omitted
  // (e.g. "Deleting is PC only." for a remote viewer).
  deleteNote?: string
}

export function DramaDetailPanel({ dramaId, onDeleted, deleteNote }: Props) {
  const [drama, setDrama] = useState<DramaDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [deleteError, setDeleteError] = useState<unknown>(null)

  const remove = () => {
    deleteDrama(dramaId).then(
      (r) => onDeleted?.(r),
      (e: unknown) => setDeleteError(e),
    )
  }

  useEffect(() => {
    // App remounts this panel per drama (key={dramaId}), so state starts
    // fresh for each one instead of being reset here.
    let cancelled = false
    api
      .getDrama(dramaId)
      .then((d) => !cancelled && setDrama(d))
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : 'Unexpected error.')
      })
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (error) return <section className="panel wide"><p className="error" role="alert">{error}</p></section>
  if (!drama) return <section className="panel wide"><p className="muted">Loading…</p></section>

  const rows: [string, string | number | null][] = [
    ['Original title', drama.title_zh],
    ['Author', credit(drama.author, drama.author_romanized)],
    ['Studio', credit(drama.studio, drama.studio_romanized)],
    ['Voice actors', credit(drama.voice_actors, drama.voice_actors_romanized)],
    ['Status', drama.status],
    ['Type', drama.media_type],
    ['Language', drama.source_language],
    ['Genre', drama.genre],
    ['Chapters', drama.chapter_count],
    ['Translation engine', drama.translation_engine],
    ['Tags', drama.custom_tags.join(', ')],
    ['Source media', drama.has_audio ? 'attached' : 'none'],
  ]

  return (
    <section className="panel wide" aria-labelledby="detail-heading">
      <h2 id="detail-heading">{drama.title_en || drama.title_zh || `Drama #${drama.id}`}</h2>
      {drama.summary && <p>{drama.summary}</p>}
      <dl>
        {rows
          .filter(([, v]) => v !== null && v !== '')
          .map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
      </dl>
      <a href={routeHref({ name: 'drama', id: dramaId, stage: null })}>Open workspace</a>
      {' · '}<a href={routeHref({ name: isComicType(drama.media_type) ? 'comic' : 'read', id: dramaId, page: null })}>Read</a>
      {!onDeleted && deleteNote && <p className="muted">{deleteNote}</p>}
      {onDeleted && !confirming && (
        <button type="button" className="danger" onClick={() => setConfirming(true)}>
          Delete drama…
        </button>
      )}
      {onDeleted && confirming && (
        <div className="delete-confirm">
          <p>This permanently deletes the drama and all its lines. Type DELETE to confirm.</p>
          <input
            aria-label="Type DELETE to confirm"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          <button type="button" className="danger" disabled={!canConfirmDelete(typed)} onClick={remove}>
            Delete permanently
          </button>
          <button type="button" className="link" onClick={() => { setConfirming(false); setTyped(''); setDeleteError(null) }}>
            Cancel
          </button>
          {deleteError instanceof ApiError && deleteError.status === 409 ? (
            <p className="error" role="alert">
              Not deleted: a background job is still running for this drama. Wait for it to
              finish or cancel it, then try again.
            </p>
          ) : (
            <ErrorBanner error={deleteError} />
          )}
        </div>
      )}
    </section>
  )
}
