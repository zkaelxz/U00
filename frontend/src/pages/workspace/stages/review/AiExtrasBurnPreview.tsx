import { useCallback, useEffect, useState } from 'react'

import { burnPreviewClipUrl, getBurnPreviewInfo, startBurnPreview } from '../../../../api/reviewExtras'
import { getAssStyleOptions } from '../../../../api/export'
import { listAllLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useReattachJob } from '../../../../hooks/useReattachJob'
import { draftStorage, readDraft } from '../../../../hooks/useStageDraft'
import { assFormFromDraft, buildAssRequest, EXPORT_DRAFT_STAGE } from '../../exportForm'
import { burnPreviewJobId } from '../../stageJobIds'
import type { AssStyleOptions } from '../../../../types/export'
import type { BurnPreviewInfo } from '../../../../types/reviewExtras'
import { JobPanel } from '../JobPanel'
import { clipCaption, resolveLineNumber } from './aiExtrasLogic'

interface Props {
  dramaId: number
}

// A few seconds of the source video around one line with the subtitles
// burned in, to check a style before a full export. One clip per project,
// replaced by the next render.
export function AiExtrasBurnPreview({ dramaId }: Props) {
  const [info, setInfo] = useState<BurnPreviewInfo | null>(null)
  const [lineText, setLineText] = useState('')
  const [lineError, setLineError] = useState<string | null>(null)
  const [pad, setPad] = useState('2')
  const [saved] = useState(() => assFormFromDraft(readDraft(draftStorage(), dramaId, EXPORT_DRAFT_STAGE)))
  const [preset, setPreset] = useState(saved?.preset || 'Clean')
  const [styleOptions, setStyleOptions] = useState<AssStyleOptions | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob([burnPreviewJobId(dramaId)], adoptJob)
  const [loadKey, setLoadKey] = useState(0)
  const reload = useCallback(() => setLoadKey((n) => n + 1), [])
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone: reload })
  const running = starting || (jobId !== null && !done && !pollError)

  useEffect(() => {
    let cancelled = false
    getBurnPreviewInfo(dramaId).then(
      (i) => !cancelled && setInfo(i),
      (e) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, loadKey])

  useEffect(() => {
    if (!saved) return
    let cancelled = false
    getAssStyleOptions().then(
      (o) => !cancelled && setStyleOptions(o),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [saved])

  const padNum = Number(pad)
  const padError =
    pad.trim() === '' || !Number.isFinite(padNum) || padNum < 0 || padNum > (info?.max_pad_seconds ?? 5)
      ? `0–${info?.max_pad_seconds ?? 5} s`
      : null
  const reason = !info
    ? null
    : !info.has_video
      ? 'This project has no source video.'
      : !info.ffmpeg_available
        ? 'Needs ffmpeg (with libass) on the PC.'
        : null

  const render = async () => {
    setError(null)
    setLineError(null)
    setStarting(true)
    try {
      const picked = resolveLineNumber(await listAllLines(dramaId), lineText)
      if ('error' in picked) {
        setLineError(picked.error)
        return
      }
      // The Export stage's style form, so the clip matches what would be burned.
      const built = saved && styleOptions ? buildAssRequest({ ...saved, preset }, styleOptions) : null
      const r = await startBurnPreview(dramaId, {
        line_id: picked.lineId,
        pad_seconds: padNum,
        preset,
        ...(built?.request && {
          style: built.request.style,
          speaker_colors: built.request.speaker_colors,
          per_speaker_colors: built.request.per_speaker_colors,
          wrap_chars_en: built.request.wrap_chars_en,
          wrap_chars_source: built.request.wrap_chars_source,
        }),
      })
      setJobId(r.job_id)
    } catch (e) {
      setError(e)
    } finally {
      setStarting(false)
    }
  }

  return (
    <Section storageKey="review.aiExtras.burn" title="Burned subtitle preview" summary={info?.clip ? clipCaption(info.clip) : 'No clip yet'}>
      <p className="muted">
        Renders a short clip around one line with the subtitles burned in (up to {info?.max_clip_seconds ?? 30} s).
        {saved && ' Uses the style settings from the Export stage.'}
      </p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (!reason && !padError && lineText.trim() && !running) void render()
        }}
      >
        <div className="review-edit-row">
          <Field label="Line" help="The line number shown in the list, e.g. 12." error={lineError}>
            <input inputMode="numeric" placeholder="#" value={lineText} onChange={(e) => setLineText(e.target.value)} />
          </Field>
          <Field label="Around it" unit="s" help="Seconds of video before and after the line." error={padError}>
            <input type="number" inputMode="decimal" step="0.5" value={pad} onChange={(e) => setPad(e.target.value)} />
          </Field>
          <Field label="Style">
            <select value={preset} onChange={(e) => setPreset(e.target.value)}>
              {(info?.presets ?? ['Clean']).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className={buttonClass('primary')} disabled={!info || !!reason || !!padError || !lineText.trim() || running} aria-describedby={reason ? 'burn-reason' : undefined}>
            {running ? 'Rendering…' : 'Render preview'}
          </button>
          {reason && (
            <span id="burn-reason" className="muted">
              {reason}
            </span>
          )}
        </div>
      </form>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <JobPanel jobId={jobId} job={job} pollError={pollError} lastRun={{ dramaId, ids: [burnPreviewJobId(dramaId)], retryFor: () => () => void render() }} />
      {info?.clip && (
        <figure className="stack" style={{ margin: 0 }} data-testid="burn-preview-clip">
          <video
            className="review-video"
            controls
            playsInline
            preload="metadata"
            src={burnPreviewClipUrl(dramaId, info.clip.created_at ?? loadKey)}
          />
          <figcaption className="muted">{clipCaption(info.clip)}</figcaption>
        </figure>
      )}
    </Section>
  )
}
