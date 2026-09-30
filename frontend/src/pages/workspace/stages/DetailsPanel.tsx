import { useEffect, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getSeries, updateDramaMetadata } from '../../../api/library'
import { getSourceConfig, updateSourceConfig } from '../../../api/source'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize, humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
import type { LibrarySeries } from '../../../types/library'
import type { SourceConfig } from '../../../types/workspace'
import { SOURCE_LANGUAGES } from '../../libraryForm'
import {
  buildDetailsPayload,
  CONTENT_MODES,
  FIELD_LABELS,
  formFromDrama,
  isEmptyPayload,
  reseedForm,
  mediaTypeOptions,
  modeLabel,
  modeUpdate,
  NEW_SERIES,
  PUBLICATION_STATUSES,
  serverFieldErrors,
  validateDetails,
  type DetailsErrors,
  type DetailsForm,
} from '../detailsForm'
import { useStage } from '../StageContext'

export function DetailsPanel() {
  const { dramaId, drama, refetchDrama } = useStage()
  const [initial, setInitial] = useState<DetailsForm>(() => formFromDrama(drama))
  const [form, setForm] = useState<DetailsForm>(initial)
  const [errors, setErrors] = useState<DetailsErrors>({})
  const [series, setSeries] = useState<LibrarySeries[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [seriesReloads, setSeriesReloads] = useState(0)

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
  const reason = !dirty ? 'Still needed: a change to save.' : null
  const title = drama.title_en || drama.title_zh || `#${dramaId}`

  const text = (k: keyof DetailsForm, help?: string, type = 'text') => (
    <Field label={FIELD_LABELS[k]} help={help} error={errors[k]}>
      <input type={type} value={form[k]} onChange={set(k)} />
    </Field>
  )
  const count = (k: 'chapter_count' | 'episode_number', help: string) => (
    <Field label={FIELD_LABELS[k]} help={help} error={errors[k]}>
      <input type="number" inputMode="numeric" min={0} step={1} value={form[k]} onChange={set(k)} />
    </Field>
  )

  return (
    <section className="panel" aria-label="Edit details">
      <Section storageKey="source.details" title="Edit details" summary={`${title} · ${modeLabel(form.media_type)} · ${humanize('language', form.source_language)}`}>
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
            <Field
              label="Series"
              help="Shares characters and glossary with other dramas in the series. A drama taken out of a private series stays private."
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
                help="Created when you save. If a series with this name already exists, the drama joins it."
                error={errors.new_series_name}
              >
                <input value={form.new_series_name} maxLength={300} onChange={set('new_series_name')} />
              </Field>
            )}
          </div>
          <div className="source-grid">
            {text('author')}
            {text('studio')}
            {text('director')}
            {text('voice_actors', 'Comma-separated.')}
          </div>
          <div className="source-grid">
            {text('genre', 'e.g. xianxia, romance, mystery.')}
            <Field label={FIELD_LABELS.publication_status} help="Whether the original is still coming out." error={errors.publication_status}>
              <select value={form.publication_status} onChange={set('publication_status')}>
                {initial.publication_status === '' && <option value="">Not set</option>}
                {PUBLICATION_STATUSES.map((p) => <option key={p} value={p}>{humanizeValue(p)}</option>)}
              </select>
            </Field>
            {count('chapter_count', 'How many chapters the original has. Leave empty if unknown.')}
            {count('episode_number', 'Orders this drama within its series, so the next episode gets this one\'s running summary. Leave empty to use the date added.')}
          </div>
          {text('source_url', 'The public listing or info page this drama came from. Shown without any ?query part, which can hold a download token.', 'url')}
          {text('custom_tags', 'Comma-separated, e.g. bl, favorite.')}
          <Field label="Summary" error={errors.summary}>
            <textarea rows={3} value={form.summary} onChange={set('summary')} />
          </Field>
          <Field
            label={FIELD_LABELS.episode_summary}
            help="Key events, open threads and character state. Given to the next episode's translation as context. Filled in after a translation run; edit it freely."
            error={errors.episode_summary}
          >
            <textarea rows={3} value={form.episode_summary} onChange={set('episode_summary')} />
          </Field>
        </form>
      </Section>
    </section>
  )
}

// Content mode and transcript mode (POST /api/source/dramas/{id}/config).
// `onSaved` lets the stage reload anything that reads transcript_mode.
export function SourceModePanel({ onSaved }: { onSaved: () => void }) {
  const { dramaId, refetchDrama } = useStage()
  const [config, setConfig] = useState<SourceConfig | null>(null)
  const [content, setContent] = useState('')
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getSourceConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        setContent(c.content_mode)
        setTranscript(c.transcript_mode)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const update = config ? modeUpdate(config, content, transcript) : {}
  const dirty = Object.keys(update).length > 0
  const reason = !config ? 'Loading…' : !dirty ? 'Still needed: a change to save.' : null

  const save = () => {
    if (!dirty) return
    setBusy(true)
    updateSourceConfig(dramaId, update).then(
      (c) => {
        setBusy(false)
        setError(null)
        setConfig(c)
        setContent(c.content_mode)
        setTranscript(c.transcript_mode)
        setNotice('Modes saved.')
        refetchDrama() // streamer_vod also sets media_type
        onSaved()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const transcriptOptions = config
    ? config.transcript_mode_options.includes(config.transcript_mode)
      ? config.transcript_mode_options
      : [config.transcript_mode, ...config.transcript_mode_options]
    : []

  return (
    <section className="panel" aria-label="Source modes">
      <Section
        storageKey="source.modes"
        title="Source modes"
        summary={config ? `${modeLabel(config.content_mode)} · ${modeLabel(config.transcript_mode)}` : undefined}
      >
        <div className="source-panel">
          <div>
            <button type="button" className="primary" disabled={!dirty || busy} onClick={save}>
              Save modes
            </button>
            {reason && <p className="muted">{reason}</p>}
          </div>
          {config && (
            <div className="source-grid">
              <Field label="Content mode" help="Streamer VOD also sets the media type to streamer vod.">
                <select value={content} onChange={(e) => { setNotice(null); setContent(e.target.value) }}>
                  {(CONTENT_MODES.includes(content) ? CONTENT_MODES : [content, ...CONTENT_MODES]).map((m) => (
                    <option key={m} value={m}>{modeLabel(m)}</option>
                  ))}
                </select>
              </Field>
              <Field
                label="Transcript mode"
                help={config.has_video_source ? undefined : 'Hardsub OCR needs a source video; upload one to enable it.'}
              >
                <select value={transcript} onChange={(e) => { setNotice(null); setTranscript(e.target.value) }}>
                  {transcriptOptions.map((m) => (
                    <option key={m} value={m}>{modeLabel(m)}</option>
                  ))}
                </select>
              </Field>
            </div>
          )}
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      </Section>
    </section>
  )
}
