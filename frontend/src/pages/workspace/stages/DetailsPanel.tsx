import { useEffect, useState, type ReactNode } from 'react'

import { ApiError } from '../../../api/client'
import { updateDramaMetadata } from '../../../api/library'
import { listAllSeries, type SeriesListItem } from '../../../api/series'
import { updateSourceConfig } from '../../../api/source'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize, humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { SOURCE_LANGUAGES } from '../../libraryForm'
import {
  buildDetailsPayload,
  CREDITS_FIELDS,
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
  unsavedTabs,
  validateDetails,
  type DetailsErrors,
  type DetailsForm,
} from '../detailsForm'
import type { MediaTab } from '../mediaTabs'
import { useStage } from '../StageContext'
import { CreditsCover } from './CreditsCoverPanel'
import { EpisodeSummaryGenerate } from './EpisodeSummaryGenerate'
import { FillFromLink } from './FillFromLink'
import { MediaTabPanel } from './MediaTabPanel'
import { ContentModeField } from './SourceModes'
import { SERIES_HELP } from '../../../helpText'

interface Props {
  tab: MediaTab
  goTab: (tab: MediaTab) => void
  // Which sub-tabs hold unsaved edits, for the dot on the tab.
  onUnsaved: (u: { details: boolean; credits: boolean }) => void
  // The lookup that fills credits (Fill in details), shown on the Credits tab.
  fillIn: ReactNode
}

// One draft for the Details and Credits sub-tabs: a Save on either saves the whole form, and switching tabs keeps it.
export function DetailsPanel({ tab, goTab, onUnsaved, fillIn }: Props) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [initial, setInitial] = useState<DetailsForm>(() => formFromDrama(drama))
  const [form, setForm] = useState<DetailsForm>(initial)
  const [errors, setErrors] = useState<DetailsErrors>({})
  const [series, setSeries] = useState<Pick<SeriesListItem, 'id' | 'name'>[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [seriesReloads, setSeriesReloads] = useState(0)
  const [moreSignal, setMoreSignal] = useState(0)
  const [creditsSignal, setCreditsSignal] = useState(0)

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
    listAllSeries().then(
      (r) => !cancelled && setSeries(r.items),
      () => undefined, // the current series still shows by id
    )
    return () => {
      cancelled = true
    }
  }, [seriesReloads])

  const payload = buildDetailsPayload(form, initial)
  const dirty = !isEmptyPayload(payload)
  const unsaved = unsavedTabs(form, initial)
  useEffect(() => {
    onUnsaved({ details: unsaved.details, credits: unsaved.credits })
  }, [onUnsaved, unsaved.details, unsaved.credits])

  const set = (k: keyof DetailsForm) => (e: { target: { value: string } }) => {
    setNotice(null)
    setErrors((x) => ({ ...x, [k]: undefined }))
    setForm((f) => ({ ...f, [k]: e.target.value }))
  }

  const save = () => {
    const bad = validateDetails(form, initial)
    setErrors(bad)
    // An error on a field inside "More details" must not stay hidden, nor one on the other sub-tab.
    if (bad.custom_tags || bad.episode_summary) setMoreSignal((n) => n + 1)
    const badKeys = Object.keys(bad) as (keyof DetailsForm)[]
    const onCredits = (k: keyof DetailsForm) => CREDITS_FIELDS.includes(k)
    if (badKeys.length && !badKeys.some((k) => onCredits(k) === (tab === 'credits'))) goTab(tab === 'credits' ? 'details' : 'credits')
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
    : [{ id: Number(form.series_id), name: `Series #${form.series_id}` }, ...series]
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

  // Add credits (shown beside the romanized credits when there are none) opens the Credits card at its first field.
  const addCredits = () => {
    setCreditsSignal((n) => n + 1)
    setTimeout(() => document.querySelector<HTMLElement>('[aria-label="Credits"] input[name="author"]')?.focus(), 50)
  }

  // Both tabs end their form with the same Save; the notice and error show on the tab in front only.
  const saveBar = (label: string) => (
    <>
      <div className="actions">
        <button type="submit" className="primary" disabled={!dirty || busy}>
          {label}
        </button>
        {reason && <span className="muted">{reason}</span>}
      </div>
      <p role="status" hidden={!notice}>{notice}</p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
  const formProps = {
    className: 'source-panel',
    noValidate: true,
    onSubmit: (e: { preventDefault: () => void }) => {
      e.preventDefault()
      save()
    },
  }
  const creditsSummary = [form.author, form.studio].filter(Boolean).join(' · ') || 'none yet'

  return (
    <>
      <MediaTabPanel id="details" active={tab}>
        <section className="panel" aria-label="Edit details">
          <Section storageKey="source.details" defaultOpen openSignal={moreSignal} title="Edit details" summary={`${title} · ${modeLabel(form.media_type)} · ${humanize('language', form.source_language)}`}>
            <form {...formProps}>
              {tab === 'details' && saveBar('Save details')}
              <div className="source-grid">
                {text('title_en')}
                {text('title_zh')}
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
                <Field label={FIELD_LABELS.publication_status} help="Whether the original is still coming out." error={errors.publication_status}>
                  <select value={form.publication_status} onChange={set('publication_status')}>
                    {initial.publication_status === '' && <option value="">Not set</option>}
                    {PUBLICATION_STATUSES.map((p) => <option key={p} value={p}>{humanizeValue(p)}</option>)}
                  </select>
                </Field>
                {kind !== 'audio' && count('chapter_count', 'How many chapters the original has. Leave empty if unknown.')}
                <Field label={FIELD_LABELS.release_date} help="When the stream aired or the title came out. Lets the Library list titles in the order to watch them. Leave empty if unknown." error={errors.release_date}>
                  <input type="date" name="release_date" value={form.release_date} onChange={set('release_date')} />
                </Field>
                {count('episode_number', 'Orders this title within its series, so the next episode gets this one\'s running summary. Leave empty to use the date added.')}
              </div>
              <Field label="Summary" error={errors.summary}>
                <textarea rows={2} value={form.summary} onChange={set('summary')} />
              </Field>
              {text('source_url', 'The page this title came from. The full link is kept for the PC owner; everyone else sees it without any ?query part, which can hold a download token.', 'url')}
              <FillFromLink
                dramaId={dramaId}
                link={form.source_url}
                savedLink={initial.source_url}
                current={form}
                onUse={(values) => {
                  setNotice(null)
                  setForm((f) => ({ ...f, ...values }))
                }}
              />
              <Section
                storageKey="source.details.more"
                openSignal={moreSignal}
                title="More details"
                summary="Tags, episode summary"
              >
                {text('custom_tags', 'Comma-separated, e.g. bl, favorite.')}
                <Field
                  label={FIELD_LABELS.episode_summary}
                  help="Key events, open threads and character state. Given to the next episode's translation as context. Filled in after a translation run; edit it freely."
                  error={errors.episode_summary}
                >
                  <textarea rows={3} value={form.episode_summary} onChange={set('episode_summary')} />
                </Field>
                <EpisodeSummaryGenerate
                  dramaId={dramaId}
                  onGenerated={(text) => set('episode_summary')({ target: { value: text } })}
                />
              </Section>
            </form>
          </Section>
        </section>
      </MediaTabPanel>
      <MediaTabPanel id="credits" active={tab}>
        <section className="panel" aria-label="Credits">
          <Section storageKey="source.credits" defaultOpen openSignal={creditsSignal} title="Credits" summary={creditsSummary}>
            <form {...formProps}>
              {tab === 'credits' && saveBar('Save credits')}
              <div className="source-grid">
                {text('author')}
                {kind !== 'novel' && text('studio')}
                {kind === 'audio' && text('director')}
                {kind === 'audio' && text('voice_actors', 'Comma-separated.')}
                {text('genre', 'For example, xianxia, romance, mystery.')}
              </div>
            </form>
          </Section>
        </section>
        <section className="panel" aria-label="Cover and romanized credits">
          <Section storageKey="source.cover" title="Cover and romanized credits" summary="Cover image, readable names">
            <CreditsCover onAddCredits={addCredits} />
          </Section>
        </section>
        {fillIn}
      </MediaTabPanel>
    </>
  )
}
