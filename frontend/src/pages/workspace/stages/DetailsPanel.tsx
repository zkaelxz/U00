import { useEffect, useRef, useState, type ReactNode } from 'react'

import { hasStatus } from '../../../api/client'
import { setDramaSeries, updateDramaMetadata } from '../../../api/library'
import { listAllSeries, type SeriesListItem } from '../../../api/series'
import { updateSourceConfig } from '../../../api/source'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize, humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
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
  unsavedTabs,
  validateDetails,
  type DetailsErrors,
  type DetailsForm,
} from '../detailsForm'
import type { MediaTab } from '../mediaTabs'
import { ADD_SOURCE_LINK_EVENT, SOURCE_URL_FIELD_ID } from '../AddSourceLink'
import { useStage, useStageFocus } from '../StageContext'
import { CreditsCover } from './CreditsCoverPanel'
import { EpisodeSummaryGenerate } from './EpisodeSummaryGenerate'
import { TopicChaptersPanel } from './TopicChaptersPanel'
import { FillFromLink } from './FillFromLink'
import { MediaTabPanel } from './MediaTabPanel'
import { TitleRestructurePanel } from './TitleRestructurePanel'
import { ContentModeField } from './SourceModes'
import { SERIES_HELP } from '../../../helpText'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'
import { useHolds } from '../../../hooks/useHolds'
import { changedKeys, useStageDraft } from '../../../hooks/useStageDraft'
import { buttonClass } from '../../../components/uiClasses'

interface Props {
  tab: MediaTab
  // Which forms hold unsaved edits, for the dot on the Details tab.
  onUnsaved: (u: { details: boolean; credits: boolean }) => void
  // The lookup that fills credits (Fill in details), shown under Credits.
  fillIn: ReactNode
}

// One draft for the Details and Credits sub-tabs: a Save on either saves the whole form, and switching tabs keeps it.
const DETAILS_ADMIN_ONLY = 'Only an admin at the main PC can change these details. You can change the series and the source language.'

export function DetailsPanel({ tab, onUnsaved, fillIn }: Props) {
  const { dramaId, drama, refetchDrama } = useStage()
  // The details route needs admin.library (an admin at the PC); the series and the source language have
  // their own routes that a title's owner may use, so only those stay editable without it.
  const ro = !useHolds('admin.library')
  const [initial, setInitial] = useState<DetailsForm>(() => formFromDrama(drama))
  // Edits not yet saved survive a stage switch or reload, like the Translate form's.
  const { draft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, 'details', initial)
  const [form, setForm] = useState<DetailsForm>(() => ({ ...initial, ...draft }))
  const [errors, setErrors] = useState<DetailsErrors>({})
  const [series, setSeries] = useState<Pick<SeriesListItem, 'id' | 'name'>[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [seriesReloads, setSeriesReloads] = useState(0)
  const [moreSignal, setMoreSignal] = useState(0)
  const [creditsSignal, setCreditsSignal] = useState(0)
  // "Add a source link" in the title header: by its address when Details was not open, by this event when it already is.
  const linkedSignal = useStageFocus('sourceLink', SOURCE_URL_FIELD_ID)
  const [pressedSignal, setPressedSignal] = useState(0)
  useEffect(() => {
    const press = () => setPressedSignal((n) => n + 1)
    window.addEventListener(ADD_SOURCE_LINK_EVENT, press)
    return () => window.removeEventListener(ADD_SOURCE_LINK_EVENT, press)
  }, [])
  const linkSignal = linkedSignal + pressedSignal
  // The press can come before the tab switch it asks for has rendered, so the request waits for Details to show.
  const answeredSignal = useRef(0)
  useEffect(() => {
    if (linkSignal === answeredSignal.current || tab !== 'details') return
    answeredSignal.current = linkSignal
    // Field gives its control the id, so the wrapper carries this one.
    const input = document.querySelector<HTMLInputElement>(`#${SOURCE_URL_FIELD_ID} input`)
    input?.focus({ preventScroll: true })
    input?.scrollIntoView({ block: 'center' })
  }, [linkSignal, tab])

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

  useCancellableEffect((cancelled) => {
    listAllSeries().then(
      (r) => !cancelled() && setSeries(r.items),
      () => undefined, // the current series still shows by id
    )
  }, [seriesReloads])

  const payload = buildDetailsPayload(form, initial)
  const dirty = !isEmptyPayload(payload)
  const unsaved = unsavedTabs(form, initial)
  useEffect(() => {
    if (dirty) saveDraft(changedKeys(form, initial))
    else clearDraft()
  }, [dirty, form, initial, saveDraft, clearDraft])
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
    // An error on a field inside "More details" must not stay hidden.
    if (bad.custom_tags || bad.episode_summary || bad.episode_brief) setMoreSignal((n) => n + 1)
    if (Object.keys(bad).length || !dirty) return
    const sent = form
    setBusy(true)
    const writes: Promise<unknown>[] = []
    // The series pair goes to its own route so a phone can still change it.
    const { series_id, new_series_name, ...rest } = payload.metadata
    if (Object.keys(rest).length) writes.push(updateDramaMetadata(dramaId, rest))
    if (series_id !== undefined || new_series_name !== undefined) writes.push(setDramaSeries(dramaId, { series_id, new_series_name }))
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
        const fields = hasStatus(e, 422) ? serverFieldErrors(e.details, e.message) : {}
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
      <input type={type} name={k} value={form[k]} onChange={set(k)} disabled={ro} />
    </Field>
  )
  const count = (k: 'chapter_count' | 'episode_number', help: string) => (
    <Field label={FIELD_LABELS[k]} help={help} error={errors[k]}>
      {/* Text, not type=number: a malformed entry must reach the whole-number check, not read as "" (clear). */}
      <input type="text" inputMode="numeric" value={form[k]} onChange={set(k)} disabled={ro} />
    </Field>
  )

  // Add credits (shown beside the romanized credits when there are none) opens the Credits card at its first field.
  const addCredits = () => {
    setCreditsSignal((n) => n + 1)
    setTimeout(() => document.querySelector<HTMLElement>('[aria-label="Credits"] input[name="author"]')?.focus(), 50)
  }

  // Both forms start with the same Save; the notice and error show under the first only, so they are not said twice.
  const saveBar = (label: string, feedback: boolean) => (
    <>
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={!dirty || busy}>
          {label}
        </button>
        {reason && <span className="muted">{reason}</span>}
      </div>
      {ro && <p className="muted">{DETAILS_ADMIN_ONLY}</p>}
      {feedback && (
        <>
          <p role="status" hidden={!notice}>{notice}</p>
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </>
      )}
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
          <Section storageKey="source.details" defaultOpen openSignal={moreSignal + linkSignal} title="Edit details" summary={`${title} · ${modeLabel(form.media_type)} · ${humanize('language', form.source_language)}`}>
            <form {...formProps}>
              {saveBar('Save details', true)}
              <div className="source-grid">
                {text('title_en')}
                {text('title_zh')}
                <Field label="Source language" error={errors.source_language}>
                  <select value={form.source_language} onChange={set('source_language')}>
                    {SOURCE_LANGUAGES.map((l) => <option key={l} value={l}>{humanize('language', l)}</option>)}
                  </select>
                </Field>
                <Field label="Media type" error={errors.media_type}>
                  <select value={form.media_type} onChange={set('media_type')} disabled={ro}>
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
                  <select value={form.publication_status} onChange={set('publication_status')} disabled={ro}>
                    {initial.publication_status === '' && <option value="">Not set</option>}
                    {PUBLICATION_STATUSES.map((p) => <option key={p} value={p}>{humanizeValue(p)}</option>)}
                  </select>
                </Field>
                {kind !== 'audio' && count('chapter_count', 'How many chapters the original has. Leave empty if unknown.')}
                <Field label={FIELD_LABELS.release_date} help="When the stream aired or the title came out. Lets the Library list titles in the order to watch them. Leave empty if unknown." error={errors.release_date}>
                  <input type="date" name="release_date" value={form.release_date} onChange={set('release_date')} disabled={ro} />
                </Field>
                {count('episode_number', 'Orders this title within its series, so the next episode gets this one\'s running summary. Leave empty to use the date added.')}
              </div>
              <Field label="Summary" error={errors.summary}>
                <textarea rows={2} value={form.summary} onChange={set('summary')} disabled={ro} />
              </Field>
              <div id={SOURCE_URL_FIELD_ID}>
                {text('source_url', 'The page this title came from. The full link is kept for the PC owner; everyone else sees it without any ?query part, which can hold a download token.', 'url')}
              </div>
              {!ro && <FillFromLink
                dramaId={dramaId}
                link={form.source_url}
                savedLink={initial.source_url}
                current={form}
                onUse={(values) => {
                  setNotice(null)
                  setForm((f) => ({ ...f, ...values }))
                }}
              />}
              <Section
                storageKey="source.details.more"
                openSignal={moreSignal}
                title="More details"
                summary="Tags, episode summary, chapters"
              >
                {text('custom_tags', 'Comma-separated, e.g. bl, favorite.')}
                <Field
                  label={FIELD_LABELS.episode_summary}
                  help="Key events, open threads and character state. Given to the next episode's translation as context. Filled in after a translation run; edit it freely."
                  error={errors.episode_summary}
                >
                  <textarea rows={3} value={form.episode_summary} onChange={set('episode_summary')} disabled={ro} />
                </Field>
                {!ro && <EpisodeSummaryGenerate
                  dramaId={dramaId}
                  onGenerated={(text) => set('episode_summary')({ target: { value: text } })}
                />}
                <strong>Topic chapters</strong>
                <p className="muted">Timed chapters for a long stream, made with the summary engine. Shown on the Watch page; the timestamp list and the subtitled-video export use them too.</p>
                <TopicChaptersPanel dramaId={dramaId} />
                <Field
                  label={FIELD_LABELS.episode_brief}
                  help="Written by one extra request that reads the whole episode before translating (Settings > Translation style: Read the whole episode before translating) and given to every batch. Edit it freely; it is used as written. Empty means the next run writes a new one. A wrong brief colours every line."
                  error={errors.episode_brief}
                >
                  <textarea rows={4} value={form.episode_brief} onChange={set('episode_brief')} disabled={ro} />
                </Field>
              </Section>
            </form>
          </Section>
        </section>
        {mediaKind(drama.media_type) !== 'audio' && <TitleRestructurePanel />}
      </MediaTabPanel>
      <MediaTabPanel id="credits" shownOn="details" active={tab}>
        <section className="panel" aria-label="Credits">
          <Section storageKey="source.credits" defaultOpen openSignal={creditsSignal} title="Credits" summary={creditsSummary}>
            <form {...formProps}>
              {ro ? <p className="muted">Only an admin at the main PC can change the credits.</p> : saveBar('Save credits', false)}
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
