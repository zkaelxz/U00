import { useEffect, useState } from 'react'

import { getAssStyleOptions, getReadiness } from '../../../api/export'
import { Badge } from '../../../components/Badge'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import type { AssStyleOptions, ExportReadiness } from '../../../types/export'
import { routeHref } from '../../../router'
import { buttonClass } from '../../../components/uiClasses'
import { browserStorage, readPref, writePref } from '../../../hooks/usePersistedState'
import { useStageDraft } from '../../../hooks/useStageDraft'
import { assFormFromDraft, buildAssRequest, emptyAssForm, EXPORT_DRAFT_STAGE, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportAss } from './ExportAss'
import { ExportJellyfin } from './ExportJellyfin'
import { ExportEpub, ExportMediaJobs, MarkExported } from './ExportMedia'
import { ExportSubtitles, type ExportFormat } from './ExportSubtitles'
import './export.css'

const FORMATS: readonly ExportFormat[] = ['srt', 'vtt', 'lrc', 'ass']
const FIELDS: readonly AssForm['field'][] = ['en', 'zh', 'bilingual']
const allowed = <T extends string>(options: readonly T[], v: unknown, fallback: T): T => options.find((o) => o === v) ?? fallback
// A title with no draft yet starts from the viewer's last format and language (rule 12).
const LAST_CHOICE_PREF = 'export.last'
const EXPORT_DRAFT_SHAPE = { fmt: 'srt' }

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

export default function ExportStage() {
  const { dramaId, drama } = useStage()
  const [readiness, setReadiness] = useState<ExportReadiness | null>(null)
  const [readinessError, setReadinessError] = useState<unknown>(null)
  const [options, setOptions] = useState<AssStyleOptions | null>(null)
  const [optionsError, setOptionsError] = useState<unknown>(null)
  const { draft, raw: rawDraft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, EXPORT_DRAFT_STAGE, EXPORT_DRAFT_SHAPE)
  const [form, setForm] = useState<AssForm>(() => {
    const kept = assFormFromDraft(rawDraft)
    if (kept) return kept
    const last = readPref(browserStorage(), LAST_CHOICE_PREF, { fmt: 'srt', field: 'en' })
    return { ...emptyAssForm(''), field: allowed(FIELDS, last.field, 'en') }
  })
  const [fmt, setFmt] = useState<ExportFormat>(() => {
    const last = readPref(browserStorage(), LAST_CHOICE_PREF, { fmt: 'srt', field: 'en' })
    return allowed(FORMATS, draft.fmt ?? last.fmt, 'srt')
  })
  useEffect(() => {
    saveDraft({ fmt, form })
    writePref(browserStorage(), LAST_CHOICE_PREF, { fmt, field: form.field })
  }, [saveDraft, fmt, form])
  const resetToDefaults = () => {
    clearDraft()
    setFmt('srt')
    setForm(emptyAssForm(options?.default_preset ?? ''))
  }

  useEffect(() => {
    let cancelled = false
    getReadiness(dramaId).then(
      (r) => !cancelled && (setReadiness(r), setReadinessError(null)),
      (e: unknown) => !cancelled && setReadinessError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  useEffect(() => {
    let cancelled = false
    getAssStyleOptions().then(
      (o) => {
        if (cancelled) return
        setOptions(o)
        setForm((f) => ({ ...f, preset: o.presets[f.preset] ? f.preset : o.default_preset }))
      },
      (e: unknown) => !cancelled && setOptionsError(e),
    )
    return () => {
      cancelled = true
    }
  }, [])

  const r = readiness
  const review: string[] = []
  if (r) {
    if (r.overlap_count > 0) review.push(plural(r.overlap_count, 'overlapping line'))
    if (r.auto_qc_issue_count > 0) review.push(plural(r.auto_qc_issue_count, 'auto-QC issue'))
    if (r.dense_line_count > 0) review.push(plural(r.dense_line_count, 'dense line'))
  }
  const untranslated = r !== null && r.total_lines > 0 && !r.fully_translated
  const assStyle = options ? <ExportAss form={form} setForm={setForm} options={options} /> : <ErrorBanner error={optionsError} />
  return (
    <div className="stage-export">
      <section className="panel" aria-label="Export">
        <h3>Export</h3>
        <ErrorBanner error={readinessError} />
        {!r && !readinessError && <div className="skeleton-block export-readiness-skeleton" aria-hidden="true" />}
        {r && (
          <>
            <p className="export-line pill-row" data-testid="readiness">
              <Badge>{plural(r.total_lines, 'line')}</Badge>{' '}
              <Badge tone={r.total_lines === 0 ? 'neutral' : r.fully_translated ? 'ok' : 'warn'}>{r.en_filled} translated</Badge>
            </p>
            {(untranslated || review.length > 0) && (
              <ul className="export-warnings" data-testid="readiness-warnings">
                {untranslated && <li className="muted">Some lines are not translated yet, so English exports will have gaps.</li>}
                {review.length > 0 && (
                  <li className="muted">
                    {review.join(', ')}. <a href={routeHref({ name: 'drama', id: dramaId, stage: 'review' })}>Open Review checks</a> to flag them.
                  </li>
                )}
              </ul>
            )}
          </>
        )}
        <ExportSubtitles
          fmt={fmt}
          setFmt={setFmt}
          form={form}
          setForm={setForm}
          options={options}
          totalLines={r ? r.total_lines : null}
        />
        <div className="actions">
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={resetToDefaults}>Reset to defaults</button>
        </div>
        <MarkExported />
      </section>
      {fmt === 'ass' && assStyle}
      <Section storageKey="export.media" title="Video and audio" summary="Burned-in video, audiobook">
        {/* The burned-in video uses the ASS style too; with ASS chosen it sits above instead. */}
        {fmt !== 'ass' && assStyle}
        <ExportMediaJobs
          request={() =>
            options ? buildAssRequest(form, options) : { error: 'The style options have not loaded yet.' }
          }
        />
      </Section>
      {drama.content_mode === 'novel_narration' && (
        <Section storageKey="export.epub" title="EPUB" summary="Novel narration">
          <ExportEpub />
        </Section>
      )}
      {/* keyed by the text choice, so a video list found for one language is not reused for another */}
      <ExportJellyfin key={form.field} field={form.field} />
    </div>
  )
}
