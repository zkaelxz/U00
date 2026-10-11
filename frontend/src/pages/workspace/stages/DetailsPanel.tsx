import { useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getSeries, updateDramaMetadata } from '../../../api/library'
import { updateSourceConfig } from '../../../api/source'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize, humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
import type { LibrarySeries } from '../../../types/library'
import { SOURCE_LANGUAGES } from '../../libraryForm'
import {
  buildDetailsPayload,
  FIELD_LABELS,
  formFromDrama,
  isEmptyPayload,
  reseedForm,
  mediaKind,
  mediaTypeOptions,
  modeLabel,
  NEW_SERIES,
  PUBLICATION_STATUSES,
  serverFieldErrors,
  validateDetails,
  type DetailsErrors,
  type DetailsForm,
} from '../detailsForm'
import { useStage } from '../StageContext'
import { CreditsCover } from './CreditsCoverPanel'
import { ContentModeField } from './SourceModes'
import { SERIES_HELP } from '../../../helpText'

export function DetailsPanel({ openSignal, onAddCredits }: { openSignal?: number; onAddCredits?: () => void }) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [initial, setInitial] = useState<DetailsForm>(() => formFromDrama(drama))
  const [form, setForm] = useState<DetailsForm>(initial)
  const [errors, setErrors] = useState<DetailsErrors>({})
  const [series, setSeries] = useState<LibrarySeries[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [seriesReloads, setSeriesReloads] = useState(0)
  const [moreSignal, setMoreSignal] = useState(0)

  // Re-seed from the drama when it changes elsewhere (Auto-fill apply, Use
  // this content type, a URL download finishing): only the fields the user
  // has edited keep their value, so a save never sends a stale one back.
  const [seededFrom, setSeededFrom] = useState(drama)
  if (seededFrom !== drama) {
    setSeededFrom(drama)
    const next = formFromDrama(drama)
    setForm(reseedForm(form, initial, next))
    setInitial(next)
  }

  useEffect(() => {
    let cancelled = false
    getSeries().then(
      (r) => !cancelled && setSeries(r.items),
      () => undefined, // the current series still shows by id
    )
    return () => {
      cancelled = true
    }
  }, [seriesReloads])

  const payload = buildDetailsPayload(form, initial)
  const dirty = !isEmptyPayload(payload)

  const set = (k: keyof DetailsForm) => (e: { target: { value: string } }) => {
    setNotice(null)
    setErrors((x) => ({ ...x, [k]: undefined }))
    setForm((f) => ({ ...f, [k]: e.target.value }))
  }

  const save = () => {
    const bad = validateDetails(form, initial)
    setErrors(bad)
    // An error on a field inside "More details" must not stay hidden.
    if (bad.source_url || bad.custom_tags || bad.episode_summary) setMoreSignal((n) => n + 1)
    if (Object.keys(bad).length || !dirty) return
    const sent = form
    setBusy(true)
    const writes: Promise<unknown>[] = []
    if (Object.keys(payload.metadata).length) writes.push(updateDramaMetadata(dramaId, payload.metadata))
    if (payload.sourceLanguage) writes.push(updateSourceConfig(dramaId, { source_language: payload.sourceLanguage }))
    Promise.all(writes).then(
      () => {
        setBusy(false)
        setError(null)
        setNotice('Details saved.')
        if (payload.metadata.new_series_name) setSeriesReloads((n) => n + 1)
        // What was sent is now saved: the next drama read replaces those
        // fields (a new series only gets its id then).
        setInitial(sent)
        refetchDrama()
      },
      (e: unknown) => {
        setBusy(false)
        const fields = e instanceof ApiError && e.status === 422 ? serverFieldErrors(e.details, e.message) : {}
        if (Object.keys(fields).length) setErrors(fields)
        else setError(e)
        refetchDrama() // one of two writes may have landed
      },
    )
  }

  const seriesOptions = series.some((s) => String(s.id) === form.series_id) || form.series_id === '' || form.series_id === NEW_SERIES
    ? series
    : [{ id: Number(form.series_id), name: `Series #${form.series_id}` } as LibrarySeries, ...series]
  // Hidden fields keep their value; only edited fields are ever sent.
  const kind = mediaKind(form.media_type)
  const reason = !dirty ? 'Still needed: a change to save.' : null
  const title = drama.title_en || drama.title_zh || `#${dramaId}`

  const text = (k: keyof DetailsForm, help?: string, type = 'text') => (
    <Field label={FIELD_LABELS[k]} help={help} error={errors[k]}>
      <input type={type} name={k} value={form[k]} onChange={set(k)} />
    </Field>
  )
  const count = (k: 'chapter_count' | 'episode_number', help: string) => (
    <Field label={FIELD_LABELS[k]} help={help} error={errors[k]}>
      {/* Text, not type=number: a malformed entry must reach the whole-number check, not read as "" (clear). */}
      <input type="text" inputMode="numeric" value={form[k]} onChange={set(k)} />
    </Field>
  )

  return (
    <section className="panel" aria-label="Edit details">
      <Section storageKey="source.details" openSignal={openSignal} title="Edit details" summary={`${title} · ${modeLabel(form.media_type)} · ${humanize('language', form.source_language)}`}>
        <form
          className="source-panel"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          <div>
            <button type="submit" className="primary" disabled={!dirty || busy}>
              Save details
            </button>
            {reason && <p className="muted">{reason}</p>}
          </div>
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
          <div className="source-grid">
            {text('title_en')}
            {text('title_zh')}
          </div>
          <div className="source-grid">
            <Field label="Source language" error={errors.source_language}>
              <select value={form.source_language} onChange={set('source_language')}>
                {SOURCE_LANGUAGES.map((l) => <option key={l} value={l}>{humanize('language', l)}</option>)}
              </select>
            </Field>
            <Field label="Media type" error={errors.media_type}>
              <select value={form.media_type} onChange={set('media_type')}>
                {mediaTypeOptions(initial.media_type).map((m) => (
                  <option key={m} value={m}>{modeLabel(m)}</option>
                ))}
              </select>
            </Field>
            {kind === 'audio' && <ContentModeField />}
            <Field
              label="Series"
              help={`${SERIES_HELP} A title taken out of a private series stays private.`}
              error={errors.series_id}
            >
              <select value={form.series_id} onChange={set('series_id')}>
                <option value="">No series</option>
                {seriesOptions.map((s) => (
                  <option key={s.id} value={String(s.id)}>{s.name}</option>
                ))}
                <option value={NEW_SERIES}>+ New series…</option>
              </select>
            </Field>
            {form.series_id === NEW_SERIES && (
              <Field
                label={FIELD_LABELS.new_series_name}
                help="Created when you save. If a series with this name already exists, the title joins it."
                error={errors.new_series_name}
              >
                <input value={form.new_series_name} maxLength={300} onChange={set('new_series_name')} />
              </Field>
            )}
          </div>
          <div className="source-grid">
            {text('author')}
            {kind !== 'novel' && text('studio')}
            {kind === 'audio' && text('director')}
            {kind === 'audio' && text('voice_actors', 'Comma-separated.')}
          </div>
          <div className="source-grid">
            {text('genre', 'For example, xianxia, romance, mystery.')}
            <Field label={FIELD_LABELS.publication_status} help="Whether the original is still coming out." error={errors.publication_status}>
              <select value={form.publication_status} onChange={set('publication_status')}>
                {initial.publication_status === '' && <option value="">Not set</option>}
                {PUBLICATION_STATUSES.map((p) => <option key={p} value={p}>{humanizeValue(p)}</option>)}
              </select>
            </Field>
            {kind !== 'audio' && count('chapter_count', 'How many chapters the original has. Leave empty if unknown.')}
            {count('episode_number', 'Orders this title within its series, so the next episode gets this one\'s running summary. Leave empty to use the date added.')}
          </div>
          <Field label="Summary" error={errors.summary}>
            <textarea rows={3} value={form.summary} onChange={set('summary')} />
          </Field>
          <Section
            storageKey="source.details.more"
            openSignal={moreSignal}
            title="More details"
            summary="Link, tags, episode summary"
            defaultOpen={!!(form.source_url || form.custom_tags || form.episode_summary)}
          >
            {text('source_url', 'The public listing or info page this title came from. Shown without any ?query part, which can hold a download token.', 'url')}
            {text('custom_tags', 'Comma-separated, e.g. bl, favorite.')}
            <Field
              label={FIELD_LABELS.episode_summary}
              help="Key events, open threads and character state. Given to the next episode's translation as context. Filled in after a translation run; edit it freely."
              error={errors.episode_summary}
            >
              <textarea rows={3} value={form.episode_summary} onChange={set('episode_summary')} />
            </Field>
          </Section>
        </form>
        <CreditsCover onAddCredits={onAddCredits} />
      </Section>
    </section>
  )
}
