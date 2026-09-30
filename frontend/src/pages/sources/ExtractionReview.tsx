/*
 * ExtractionReview: the review step between a pasted-URL preview and the
 * write (parity SO10). A URL import opens one for its drama instead of
 * writing when Baihe is unsure (low confidence), when "Check before
 * importing" is on, or in Sources diagnostics mode. Nothing here fetches
 * the site again: the server kept the page it read.
 *
 *   novel  pick the part of the page that holds the chapter, what to leave
 *          out, the chapter title, the next/previous links and where the
 *          chapter number comes from; "Re-run with these corrections"
 *   comic  mark each image's role and number the pages; "Apply these corrections"
 *
 * A novel import that followed next-chapter links also lists every page it
 * read (title, length, host), each with a checkbox: the ticked pages are
 * imported in reading order. Corrections apply to the first page only.
 *
 * Then import (the per-drama sourceimport_ job), save the corrections as
 * the site's profile, or approve a suggested profile (both PC only). Every
 * choice is an option the server offered, sent by id or selector. A
 * review that changed elsewhere answers 409 and is reloaded.
 */
import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../api/client'
import {
  approveReviewProfile, getExtractionReview, rerunComic, rerunNovel, reviewImageUrl, saveReviewProfile, startReviewImport,
} from '../../api/sourcesExtraction'
import { sourceImportJobId } from '../../api/sourcesImport'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type {
  ExtractionComic, ExtractionFollow, ExtractionNovel, ExtractionReview as Review, ProfileSaved, ReviewImportResult,
} from '../../types/sourcesExtraction'
import {
  REVIEW_NOTE, bucketLabel, bucketTone, canImport, changedImages, containerLabel, duplicatePages, exclusionsFor,
  fieldLabel, followPageLabel, followStopText, importLabel, importPages, nextPageNumber, novelForm, pickedChars,
  profileSavedText, reviewImportText, reviewWhy, roleLabel, withContainer,
} from './extractionFormat'
import { describeSourceError, percent } from './sourcesFormat'
import { useSourcesJob } from './useSourcesJob'
import './extraction.css'

type Props = {
  dramaId: number
  onClose: () => void
}

const isStale = (e: unknown) => e instanceof ApiError && e.status === 409

export function ExtractionReview({ dramaId, onClose }: Props) {
  const [review, setReview] = useState<Review | null>(null)
  const [gone, setGone] = useState(false)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<unknown>(null)
  const [saved, setSaved] = useState<string | null>(null)
  // Page ids of a followed import left out of the import (ids stay the same across re-runs).
  const [unticked, setUnticked] = useState<ReadonlySet<number>>(() => new Set())
  const remote = usePcOnly() === 'remote'
  const job = useSourcesJob<ReviewImportResult>(sourceImportJobId(dramaId), { reattachOn409: false })
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'review_import' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null

  const load = useCallback(() => {
    getExtractionReview(dramaId).then(
      (r) => {
        setReview(r)
        setGone(false)
        setLoadError(null)
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 404) setGone(true)
        else setLoadError(e)
      },
    )
  }, [dramaId])

  useEffect(load, [load])

  // Re-run / apply: the server answers with the new review.
  const change = (p: () => Promise<Review>) => {
    setBusy(true)
    setActionError(null)
    setSaved(null)
    p().then(
      (r) => {
        setBusy(false)
        setReview(r)
      },
      (e: unknown) => {
        setBusy(false)
        setActionError(e)
        if (isStale(e)) load()
      },
    )
  }

  const profile = (p: () => Promise<ProfileSaved>, after?: () => void) => {
    setBusy(true)
    setActionError(null)
    setSaved(null)
    p().then(
      (v) => {
        setBusy(false)
        setSaved(profileSavedText(v))
        after?.()
      },
      (e: unknown) => {
        setBusy(false)
        setActionError(e)
        if (isStale(e)) load()
      },
    )
  }

  if (result) {
    return (
      <section className="sources-card extraction-review" aria-label="Review extraction">
        <p data-testid="review-import-result">
          {reviewImportText(result)}{' '}
          <ButtonLink href={result.content_type === 'comic' ? `#/comic/${dramaId}` : `#/drama/${dramaId}/source`} size="sm">
            {result.content_type === 'comic' ? 'Open pages' : 'Open workspace'}
          </ButtonLink>
        </p>
      </section>
    )
  }

  if (gone || (!review && loadError)) {
    return (
      <section className="sources-card extraction-review" aria-label="Review extraction">
        {gone ? <p className="muted">This review has ended (it was imported, replaced or timed out). Import the link again to start over.</p>
          : <ErrorBanner error={loadError} describe={{ serverText: true }} />}
        <div className="actions">
          <button type="button" className={buttonClass('ghost')} onClick={onClose}>
            Close
          </button>
        </div>
      </section>
    )
  }
  if (!review) return <p className="muted">Loading the review…</p>

  const locked = busy || running
  const pending = review.report.pending_profile
  return (
    <section className="sources-card extraction-review" aria-label="Review extraction" data-testid="extraction-review">
      <div className="sources-series-head">
        <h3>Review extraction</h3>
        <Badge tone={bucketTone(review.confidence.overall.bucket)}>
          {bucketLabel(review.confidence.overall.bucket)} confidence
        </Badge>
      </div>
      <p>{reviewWhy(review.why)}</p>
      <p className="muted">{REVIEW_NOTE}</p>
      {review.display_url && <p className="muted sources-link">{review.display_url}</p>}

      <details className="extraction-details">
        <summary>Confidence per field</summary>
        <p className="muted">Checked by Baihe itself, not taken from what the AI said about its own answer.</p>
        <ul>
          {review.confidence.fields.map((f) => (
            <li key={f.field}>
              <Badge tone={bucketTone(f.bucket)}>{bucketLabel(f.bucket)}</Badge> {fieldLabel(f.field)}
              {f.value ? `: ${f.value}` : ''}
              {f.checks.length > 0 && <span className="muted"> ({f.checks.join('; ')})</span>}
            </li>
          ))}
        </ul>
      </details>
      <details className="extraction-details">
        <summary>How the page was read</summary>
        {review.report.headline && <p>{review.report.headline}</p>}
        <p className="muted">
          AI calls: {review.report.llm_calls}
          {review.report.cache_hit ? ' (an earlier result for this exact page was reused)' : ''}
        </p>
        {review.report.profile && <p className="muted">{review.report.profile}</p>}
        {review.report.lines.length > 0 && (
          <ul>
            {review.report.lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
      </details>

      {pending && (
        <div className="extraction-pending" role="group" aria-label="Suggested site profile">
          <p>
            Baihe worked out a profile for this site ({bucketLabel(pending.bucket).toLowerCase()} confidence). Approve it
            and the site’s next chapter is read with it, with no AI call.
          </p>
          {remote ? (
            <p className="muted">Approving a site profile is PC only.</p>
          ) : (
            <button
              type="button"
              className={buttonClass('secondary')}
              disabled={locked}
              onClick={() => profile(() => approveReviewProfile(dramaId, review.revision), load)}
            >
              Approve the suggested profile
            </button>
          )}
        </div>
      )}

      {review.novel && (
        <NovelReview
          key={review.revision}
          novel={review.novel}
          disabled={locked}
          onRerun={(form) => change(() => rerunNovel(dramaId, { revision: review.revision, ...form }))}
        />
      )}
      {review.follow && (
        <FollowedPages
          follow={review.follow}
          unticked={unticked}
          disabled={locked}
          onToggle={(id, on) =>
            setUnticked((u) => {
              const next = new Set(u)
              if (on) next.delete(id)
              else next.add(id)
              return next
            })
          }
        />
      )}
      {review.comic && (
        <ComicReview
          key={review.revision}
          dramaId={dramaId}
          comic={review.comic}
          disabled={locked}
          onApply={(images) => change(() => rerunComic(dramaId, review.revision, images))}
        />
      )}

      <ErrorBanner error={actionError} onDismiss={() => setActionError(null)} describe={{ serverText: true }} />
      {saved && <p role="status">{saved}</p>}

      <div className="actions extraction-actions">
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={locked || !canImport(review, unticked)}
          onClick={() => job.start(() => startReviewImport(dramaId, review.revision, importPages(review, unticked)))}
        >
          {running ? 'Importing…' : importLabel(review, unticked)}
        </button>
        {remote ? (
          <span className="muted">Saving a site profile is PC only.</span>
        ) : (
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={locked || !review.can_save_profile}
            title={review.can_save_profile ? undefined : 'Re-run with your corrections first.'}
            onClick={() => profile(() => saveReviewProfile(dramaId, review.revision))}
          >
            Save corrections as the site’s profile
          </button>
        )}
        <button type="button" className={buttonClass('ghost')} disabled={running} onClick={onClose}>
          Close
        </button>
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      <div aria-live="polite">
        {running && <p>{job.message || 'Importing…'}{percent(job.progress)}</p>}
        {failed && <p className="warn" role="alert">{describeSourceError(failed, 'The site').text}</p>}
      </div>
    </section>
  )
}

function FollowedPages({ follow, unticked, disabled, onToggle }: {
  follow: ExtractionFollow
  unticked: ReadonlySet<number>
  disabled: boolean
  onToggle: (id: number, on: boolean) => void
}) {
  return (
    <div className="extraction-follow" role="group" aria-label="Pages read">
      <fieldset className="extraction-fieldset">
        <legend>Pages to import, in reading order</legend>
        {follow.pages.map((p) => (
          <label key={p.id}>
            <input
              type="checkbox"
              checked={!unticked.has(p.id)}
              disabled={disabled}
              onChange={(e) => onToggle(p.id, e.target.checked)}
            />
            {followPageLabel(p)}
            {p.id === 0 ? ' (the page shown above)' : ''}
          </label>
        ))}
      </fieldset>
      <p className="muted" data-testid="follow-stop">
        {followStopText(follow)} Ticked: {pickedChars(follow, unticked).toLocaleString('en-US')} characters.
      </p>
    </div>
  )
}

function NovelReview({ novel, disabled, onRerun }: {
  novel: ExtractionNovel
  disabled: boolean
  onRerun: (form: ReturnType<typeof novelForm>) => void
}) {
  const [form, setForm] = useState(() => novelForm(novel))
  const exclusions = exclusionsFor(novel, form.content_selector)
  const linkLabel = (l: ExtractionNovel['links'][number]) => `${l.text || '(no text)'}${l.url ? ` → ${l.url}` : ''}`
  const toggleEx = (sel: string, on: boolean) =>
    setForm((f) => ({
      ...f,
      exclude_selectors: on ? [...f.exclude_selectors.filter((x) => x !== sel), sel] : f.exclude_selectors.filter((x) => x !== sel),
    }))

  return (
    <div className="extraction-novel" role="group" aria-label="Chapter text">
      <p className="sources-meta">
        {novel.chapter_title || 'No chapter title found'} · {novel.char_count.toLocaleString('en-US')} characters
      </p>
      <pre className="extraction-preview" tabIndex={0} aria-label="Extracted text (preview)">
        {novel.text_preview || '(no text)'}
      </pre>
      {novel.containers.length === 0 ? (
        <p className="muted">There are no parts of this page to choose from.</p>
      ) : (
        <>
          <Field label="The chapter text is in">
            <select
              value={form.content_selector}
              disabled={disabled}
              onChange={(e) => setForm((f) => withContainer(f, novel, e.target.value))}
            >
              {novel.containers.map((c) => (
                <option key={c.selector} value={c.selector}>
                  {containerLabel(c)}
                </option>
              ))}
            </select>
          </Field>
          {exclusions.length > 0 && (
            <fieldset className="extraction-fieldset">
              <legend>Leave out (navigation, comments, ads)</legend>
              {exclusions.map((x) => (
                <label key={x.selector}>
                  <input
                    type="checkbox"
                    checked={form.exclude_selectors.includes(x.selector)}
                    disabled={disabled}
                    onChange={(e) => toggleEx(x.selector, e.target.checked)}
                  />
                  {x.preview || x.selector}
                </label>
              ))}
            </fieldset>
          )}
          <Field label="Chapter title">
            <select
              value={form.title_block ?? ''}
              disabled={disabled}
              onChange={(e) => setForm((f) => ({ ...f, title_block: e.target.value || null }))}
            >
              <option value="">(none)</option>
              {novel.headings.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.text}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Next-chapter link">
            <select
              value={form.next_link ?? ''}
              disabled={disabled}
              onChange={(e) => setForm((f) => ({ ...f, next_link: e.target.value || null }))}
            >
              <option value="">(none)</option>
              {novel.links.map((l) => (
                <option key={l.id} value={l.id}>
                  {linkLabel(l)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Previous-chapter link">
            <select
              value={form.previous_link ?? ''}
              disabled={disabled}
              onChange={(e) => setForm((f) => ({ ...f, previous_link: e.target.value || null }))}
            >
              <option value="">(none)</option>
              {novel.links.map((l) => (
                <option key={l.id} value={l.id}>
                  {linkLabel(l)}
                </option>
              ))}
            </select>
          </Field>
          <fieldset className="extraction-fieldset extraction-radios">
            <legend>Chapter number comes from</legend>
            {(['title', 'url'] as const).map((v) => (
              <label key={v}>
                <input
                  type="radio"
                  name="extraction-number-from"
                  checked={form.number_from === v}
                  disabled={disabled}
                  onChange={() => setForm((f) => ({ ...f, number_from: v }))}
                />
                {v === 'title' ? 'The chapter title' : 'The link'}
              </label>
            ))}
          </fieldset>
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={disabled} onClick={() => onRerun(form)}>
              Re-run with these corrections
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function ComicReview({ dramaId, comic, disabled, onApply }: {
  dramaId: number
  comic: ExtractionComic
  disabled: boolean
  onApply: (images: ReturnType<typeof changedImages>) => void
}) {
  const [edits, setEdits] = useState<Record<number, { role: string; page: number }>>({})
  const changes = changedImages(comic.images, edits)
  const dupes = duplicatePages(comic.images, edits)
  const current = (id: number) => edits[id] ?? comic.images.find((i) => i.id === id)!
  const edit = (id: number, patch: Partial<{ role: string; page: number }>) =>
    setEdits((e) => {
      const base = e[id] ?? { role: current(id).role, page: current(id).page }
      const next = { ...base, ...patch }
      if (next.role !== 'content') next.page = 0
      // Newly marked as a page: number it after the last one.
      else if (patch.role && next.page === 0) next.page = nextPageNumber(comic.images, e)
      return { ...e, [id]: next }
    })

  return (
    <div className="extraction-comic" role="group" aria-label="Page images">
      <p className="sources-meta">
        {comic.page_count} of {comic.images.length} images are marked as pages. Number the pages in reading order (0 = not a
        page).
      </p>
      <ul className="extraction-images">
        {comic.images.map((img, n) => {
          const c = current(img.id)
          const label = `image ${n + 1}`
          return (
            <li key={img.id} className="extraction-image">
              <div className="extraction-thumb">
                {img.has_image ? (
                  <img src={reviewImageUrl(dramaId, img.id)} alt={`Image ${n + 1}`} loading="lazy" decoding="async" />
                ) : (
                  <span className="muted">No preview</span>
                )}
              </div>
              <div className="extraction-image-text">
                <span className="sources-link">{img.display_url ?? `Image ${n + 1}`}</span>
                <span className="muted">
                  {img.width && img.height ? `${img.width}×${img.height}` : 'size unknown'}
                  {img.reason ? ` · ${img.reason}` : ''}
                </span>
              </div>
              <div className="extraction-image-controls">
                <select aria-label={`Role for ${label}`} value={c.role} disabled={disabled} onChange={(e) => edit(img.id, { role: e.target.value })}>
                  {comic.roles.map((r) => (
                    // An image that couldn't be downloaded or read can't be a page (the server refuses it too).
                    <option key={r} value={r} disabled={r === 'content' && !img.has_image}>
                      {roleLabel(r)}
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={500}
                  aria-label={`Page number for ${label}`}
                  value={c.page}
                  disabled={disabled || c.role !== 'content'}
                  onChange={(e) => edit(img.id, { page: Math.max(0, Math.min(500, Math.trunc(Number(e.target.value) || 0))) })}
                />
              </div>
            </li>
          )
        })}
      </ul>
      {dupes.length > 0 && <p className="warn">Page {dupes.join(', ')} is used more than once; those keep their order on the page.</p>}
      <div className="actions">
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={disabled || changes.length === 0}
          onClick={() => onApply(changes)}
        >
          Apply these corrections
        </button>
      </div>
    </div>
  )
}
