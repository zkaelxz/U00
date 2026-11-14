import { useState } from 'react'

import { api, ApiError, hasStatus } from '../api/client'
import { deleteDrama, duplicateDrama, openDramaFolder } from '../api/library'
import type { DramaDetail } from '../api/types'
import { engineLabel } from '../labels'
import type { DramaDeleteResult } from '../types/library'
import { canConfirmDelete } from '../pages/libraryForm'
import { Badge } from './Badge'
import { ButtonLink } from './Button'
import { ErrorBanner } from './ErrorBanner'
import { DramaCaptureNote } from './DramaCaptureNote'
import { DramaSeriesRow } from './DramaSeriesRow'
import { OpenFolderButton } from './OpenFolderButton'
import { readHref, workspaceHref } from './libraryView'
import { buttonClass } from './uiClasses'
import { useCancellableEffect } from '../hooks/useCancellableEffect'
import { useHolds } from '../hooks/useHolds'

function credit(name: string | null, romanized: string | null) {
  if (!name) return null
  return romanized && romanized !== name ? `${name} (${romanized})` : name
}

interface Props {
  dramaId: number
  // When given, the panel offers a typed-confirmation delete.
  onDeleted?: (result: DramaDeleteResult) => void
}

// The body of the Library's drama details Sheet (the Sheet gives the title).
export function DramaDetailPanel({ dramaId, onDeleted }: Props) {
  const [drama, setDrama] = useState<DramaDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [deleteError, setDeleteError] = useState<unknown>(null)
  const [deleting, setDeleting] = useState(false)
  const [moreSummary, setMoreSummary] = useState(false)
  const [copying, setCopying] = useState(false)
  // The copy route needs library.create, which members don't hold.
  const canCopy = useHolds('library.create')
  const [copyError, setCopyError] = useState<unknown>(null)

  const copy = () => {
    setCopying(true)
    setCopyError(null)
    duplicateDrama(dramaId).then(
      (d) => window.location.assign(workspaceHref(d.id)),
      (e: unknown) => {
        setCopyError(e)
        setCopying(false)
      },
    )
  }

  // One request per confirmation: a second click while the first is in flight
  // sent a second delete for a title the first was already removing.
  const remove = () => {
    if (deleting) return
    setDeleting(true)
    setDeleteError(null)
    deleteDrama(dramaId).then(
      (r) => onDeleted?.(r),
      (e: unknown) => {
        setDeleteError(e)
        setDeleting(false)
      },
    )
  }

  useCancellableEffect((cancelled) => {
    // The page remounts this panel per drama (key={dramaId}), so state starts
    // fresh for each one instead of being reset here.
    api
      .getDrama(dramaId)
      .then((d) => !cancelled() && setDrama(d))
      .catch((e: unknown) => {
        if (!cancelled()) setError(e instanceof ApiError ? e.message : 'Unexpected error.')
      })
  }, [dramaId])

  if (error) return <p className="error" role="alert">{error}</p>
  if (!drama) return <p className="muted">Loading…</p>

  const rows: [string, string | number | null][] = [
    ['Author', credit(drama.author, drama.author_romanized)],
    ['Studio', credit(drama.studio, drama.studio_romanized)],
    ['Director', credit(drama.director, drama.director_romanized)],
    ['Voice actors', credit(drama.voice_actors, drama.voice_actors_romanized)],
    ['Genre', drama.genre],
    ['Chapters', drama.chapter_count],
    ['Translation engine', engineLabel(drama.translation_engine)],
    ['Tags', drama.custom_tags.join(', ')],
    ['Source media', drama.has_audio ? 'Attached' : 'None'],
  ]
  const longSummary = (drama.summary ?? '').length > 280

  return (
    <div className="drama-detail" data-testid="drama-detail">
      {drama.title_en && drama.title_zh && <p className="drama-detail-orig">{drama.title_zh}</p>}
      <div className="pill-row">
        {drama.status && <Badge kind="status" value={drama.status} />}
        {drama.media_type && <Badge kind="mediaType" value={drama.media_type} />}
        {drama.source_language && <Badge kind="language" value={drama.source_language} />}
      </div>
      {drama.summary && (
        <div>
          <p className={longSummary && !moreSummary ? 'drama-detail-summary clamped' : 'drama-detail-summary'}>{drama.summary}</p>
          {longSummary && (
            <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={moreSummary} onClick={() => setMoreSummary((v) => !v)}>
              {moreSummary ? 'Less' : 'More'}
            </button>
          )}
        </div>
      )}
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
      <DramaSeriesRow drama={drama} onChanged={setDrama} />
      <DramaCaptureNote dramaId={dramaId} />
      <div className="actions drama-detail-actions">
        <ButtonLink variant="primary" href={workspaceHref(dramaId)}>Open workspace</ButtonLink>
        <ButtonLink href={readHref(drama)}>Read</ButtonLink>
        <OpenFolderButton open={() => openDramaFolder(dramaId)} title="Show this title's files on the PC" />
        {canCopy && (
          <button type="button" className={buttonClass()} disabled={copying} onClick={copy}
            title="Make a separate copy with its own files and lines, so test runs don't change this title">
            {copying ? 'Copying…' : 'Make test copy'}
          </button>
        )}
      </div>
      <ErrorBanner error={copyError} />
      <div className="drama-detail-danger">
        {onDeleted && !confirming && (
          <button type="button" className={buttonClass('danger', 'sm')} onClick={() => setConfirming(true)}>
            Delete title…
          </button>
        )}
        {onDeleted && confirming && (
          <div className="delete-confirm">
            <p>This permanently deletes the title and all its lines. Type DELETE to confirm.</p>
            <input
              aria-label="Type DELETE to confirm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
            <div className="actions">
              <button type="button" className={buttonClass('danger')} disabled={deleting || !canConfirmDelete(typed)} onClick={remove}>
                {deleting ? 'Deleting…' : 'Delete permanently'}
              </button>
              <button type="button" className={buttonClass('ghost')} disabled={deleting} onClick={() => { setConfirming(false); setTyped(''); setDeleteError(null) }}>
                Cancel
              </button>
            </div>
            {hasStatus(deleteError, 409) ? (
              <p className="error" role="alert">
                Not deleted: a background job is still running for this title. Wait for it to
                finish or cancel it, then try again.
              </p>
            ) : (
              <ErrorBanner error={deleteError} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
