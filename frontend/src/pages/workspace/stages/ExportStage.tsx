import { useEffect, useState, type ReactNode } from 'react'

import { useResetUndo } from '../../../hooks/useResetUndo'
import { ResetButton } from '../../../components/ResetButton'
import { getAssStyleOptions, getReadiness } from '../../../api/export'
import { Badge } from '../../../components/Badge'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { RetryBanner } from '../../../components/RetryBanner'
import type { AssStyleOptions, ExportReadiness } from '../../../types/export'
import { routeHref } from '../../../router'
import { browserStorage, readPref, writePref } from '../../../hooks/usePersistedState'
import { useReadiness } from '../../../hooks/useReadiness'
import { useStageDraft } from '../../../hooks/useStageDraft'
import { assFormFromDraft, buildAssRequest, emptyAssForm, EXPORT_DRAFT_STAGE, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportAss } from './ExportAss'
import { ExportJellyfin } from './ExportJellyfin'
import { PreflightCard } from '../../preflight/PreflightCard'
import { NeedsInstall } from '../../readiness/NeedsInstall'
import { libassNeed, NO_LIBASS } from '../../readiness/needs'
import { exportBlockOrder, exportBlockReason, type ExportBlockId } from '../exportBlocks'
import { TITLE_KIND_LABEL, titleKind } from '../titleKind'
import { useExportInputs } from '../useExportInputs'
import { AudiobookBlock, BurnedVideoBlock, DubbedVideoBlock, ExportEpub, MarkExported, SoftsubVideoBlock, type StartError } from './ExportMedia'
import { ExportBlock } from './ExportBlock'
import { ExportSubtitles, type ExportFormat } from './ExportSubtitles'
import { SourceChangedWarning } from './SourceChangedWarning'
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
  const [readinessTry, setReadinessTry] = useState(0)
  const [options, setOptions] = useState<AssStyleOptions | null>(null)
  const [optionsError, setOptionsError] = useState<unknown>(null)
  const [startError, setStartError] = useState<StartError | null>(null)
  const kind = titleKind(drama.media_type, drama.content_mode)
  const inputs = useExportInputs(dramaId, drama.content_mode === 'novel_narration')
  const { readiness: setup } = useReadiness()
  // A missing FFmpeg has its own card above; this is only the build that lacks libass.
  const libass = setup?.ffmpeg ? libassNeed(setup) : null
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
  const resetUndo = useResetUndo()
  const resetToDefaults = () => {
    const prev = { fmt, form }
    resetUndo.reset(() => {
      clearDraft()
      setFmt('srt')
      setForm(emptyAssForm(options?.default_preset ?? ''))
    }, () => {
      setFmt(prev.fmt)
      setForm(prev.form)
    })
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
  }, [dramaId, readinessTry])

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
    if (r.auto_qc_issue_count > 0) review.push(plural(r.auto_qc_issue_count, 'auto-QC issue'))
    if (r.dense_line_count > 0) review.push(plural(r.dense_line_count, 'dense line'))
  }
  const untranslated = r !== null && r.total_lines > 0 && !r.fully_translated
  const assStyle = options ? <ExportAss form={form} setForm={setForm} options={options} /> : <ErrorBanner error={optionsError} />
  const mediaRequest = () =>
    options ? buildAssRequest(form, options) : { error: 'The style options have not loaded yet.' }
  const share = (id: ExportBlockId) => ({
    startError, onStartError: setStartError,
    reason: exportBlockReason(id, inputs) ?? (id === 'burned_video' && libass ? NO_LIBASS : null),
  })
  const blocks: Record<ExportBlockId, ReactNode> = {
    subtitles: (
      <ExportBlock key="subtitles" id="subtitles" hint="SRT, VTT, LRC or ASS">
        <ExportSubtitles
          fmt={fmt}
          setFmt={setFmt}
          form={form}
          setForm={setForm}
          options={options}
          totalLines={r ? r.total_lines : null}
        />
        {fmt === 'ass' && assStyle}
        <div className="actions">
          <ResetButton offered={resetUndo.offered} onReset={resetToDefaults} onUndo={resetUndo.undo} />
        </div>
      </ExportBlock>
    ),
    softsub_video: <SoftsubVideoBlock key="softsub_video" {...share('softsub_video')} />,
    burned_video: (
      <BurnedVideoBlock key="burned_video" request={mediaRequest} {...share('burned_video')}>
        {libass && <NeedsInstall need={libass} />}
        {/* The ASS style is one form for the subtitle file and this video; it renders once, in the subtitle file block when ASS is chosen there. */}
        {fmt === 'ass' ? <p className="muted">Uses the ASS style set in Subtitle file.</p> : assStyle}
      </BurnedVideoBlock>
    ),
    dubbed_video: <DubbedVideoBlock key="dubbed_video" {...share('dubbed_video')} />,
    audiobook: <AudiobookBlock key="audiobook" {...share('audiobook')} />,
    epub: <ExportEpub key="epub" reason={exportBlockReason('epub', inputs)} />,
  }
  return (
    <div className="stage-export">
      <section className="panel" aria-label="Export">
        <h3>Export</h3>
        <p className="export-line"><Badge>{TITLE_KIND_LABEL[kind]}</Badge></p>
        <RetryBanner error={readinessError} what="the export checks" onRetry={() => { setReadinessError(null); setReadinessTry((n) => n + 1) }} />
        {!r && !readinessError && <div className="skeleton-block export-readiness-skeleton" aria-hidden="true" />}
        {r && (
          <>
            <p className="export-line pill-row" data-testid="readiness">
              <Badge>{plural(r.total_lines, 'line')}</Badge>{' '}
              <Badge tone={r.total_lines === 0 ? 'neutral' : r.fully_translated ? 'ok' : 'warn'}>{r.en_filled} translated</Badge>
            </p>
            {(untranslated || review.length > 0 || r.overlap_count > 0) && (
              <ul className="export-warnings" data-testid="readiness-warnings">
                {untranslated && <li className="muted">Some lines are not translated yet, so English exports will have empty cues for them.</li>}
                {r.overlap_count > 0 && (
                  <li className="muted">
                    {plural(r.overlap_count, 'cue')} overlap; both stay fully visible in ASS and VTT and are shortened in SRT.{' '}
                    <a href={routeHref({ name: 'drama', id: dramaId, stage: 'review' })}>Open Review checks</a> to flag them.
                  </li>
                )}
                {review.length > 0 && (
                  <li className="muted">
                    {review.join(', ')}. <a href={routeHref({ name: 'drama', id: dramaId, stage: 'review' })}>Open Review checks</a> to flag them.
                  </li>
                )}
              </ul>
            )}
          </>
        )}
        <SourceChangedWarning dramaId={dramaId} />
        {inputs.ffmpegFound === false && <PreflightCard needs={['ffmpeg']} />}
        <MarkExported />
      </section>
      {exportBlockOrder(kind).map((id) => blocks[id])}
      <ExportJellyfin field={form.field} inputs={inputs} assRequest={mediaRequest} />
    </div>
  )
}
