import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { ApiError, withSignal } from '../../api/client'
import { getSubtitleText, markExported } from '../../api/export'
import { cancelJob, getJob } from '../../api/jobs'
import { createDrama } from '../../api/library'
import { translateApi } from '../../api/translate'
import { getTranslateConfig, startTranslateRun } from '../../api/translateStage'
import { uploadAndTranscribe } from '../../api/workspace'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { downloadText } from '../../components/downloadText'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useLoad } from '../../hooks/useLoad'
import { usePersistedState } from '../../hooks/usePersistedState'
import { useReattachJob } from '../../hooks/useReattachJob'
import { routeHref } from '../../router'
import { buttonClass } from '../../components/uiClasses'
import { engineLabel, languageLabel } from '../../labels'
import type { JobRecord } from '../../types/jobs'
import { PreflightCard } from '../preflight/PreflightCard'
import type { PreflightRow } from '../preflight/preflightModel'
import { UPLOAD_EXTENSIONS } from '../workspace/sourceForm'
import { STAGE_LABELS, type StageId } from '../workspace/stages'
import { buildRunBody, initialForm } from '../workspace/translateForm'
import { exportFilename } from '../workspace/exportForm'
import {
  IDLE, STEPS, STEP_LABEL, STEP_STAGE, blockerText, flowReducer, mediaTypeFor, optionsSummary, parseSaved, percentText,
  reattachIds, savedFor, stepForJob, titleFor, type FlowStep,
} from './makeSubtitlesFlow'
import './makeSubtitles.css'

const LANGUAGES = ['zh', 'ja', 'ko']
const VARIANTS = ['en-US', 'en-GB', 'en-AU']
const SRT_MIME = 'application/x-subrip'

const failed = (job: JobRecord) => job.status === 'error' || job.outcome === 'failed'
const lostRun = () =>
  new ApiError(404, { code: 'not_found', message: 'Lost track of this run while the page was closed. Open the title to see how far it got.' })

// File to English SRT in one go. The stage stepper stays the way to fix a
// step that went wrong: a failure links to the stage that owns it.
export function MakeSubtitles() {
  const [flow, dispatch] = useReducer(flowReducer, IDLE)
  const [saved, setSaved] = usePersistedState<unknown>('makeSubtitles.run', null)
  const [language, setLanguage] = usePersistedState('makeSubtitles.language', 'zh')
  const [pickedEngine, setPickedEngine] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [variant, setVariant] = useState('')
  const [blockers, setBlockers] = useState<PreflightRow[]>([])
  const [preflightOk, setPreflightOk] = useState<boolean | null>(null)
  const [jobId, setJobId, runKey, adopt] = useJobRun()
  const [srt, setSrt] = useState<string | null>(null)
  // True only for a run found in storage: a fresh run has no earlier job to look for.
  const [resuming, setResuming] = useState(false)
  const [downloadError, setDownloadError] = useState<unknown>(null)
  const [cancelError, setCancelError] = useState<unknown>(null)
  // Why the form is showing again with a title already in the Library (null dramaId: nothing was created).
  const [note, setNote] = useState<{ kind: 'cancelled' | 'upload-lost'; dramaId: number | null } | null>(null)
  const uploadAbort = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const status = useRef<HTMLDivElement>(null)

  const engines = useLoad(translateApi.engineList, 0)
  const list = engines.data?.items ?? []
  const defaultEngine = list.find((e) => e.name === engines.data?.default_engine)?.name ?? list[0]?.name ?? ''
  const engine = pickedEngine ?? defaultEngine

  // Read inside job callbacks, which outlive the render that created them.
  const run = useRef<{ title: string; engine: string; variant: string; dramaId: number | null }>({ title: '', engine: '', variant: '', dramaId: null })
  const dramaId = flow.phase === 'idle' ? null : flow.dramaId
  useEffect(() => { run.current = { title: file ? titleFor(file.name, title) : title, engine, variant, dramaId } })
  const stepNow = flow.phase === 'running' ? flow.step : null

  useEffect(() => { setSaved(savedFor(flow)) }, [flow]) // setSaved is a fresh function each render
  const resumed = useRef(false)
  useEffect(() => {
    if (resumed.current) return
    resumed.current = true
    const s = parseSaved(saved)
    // A run that died before the upload finished has nothing to follow.
    if (s && s.step !== 'upload') { setResuming(true); dispatch({ type: 'resume', saved: s }) }
    else if (s) setNote({ kind: 'upload-lost', dramaId: s.dramaId })
  }, [saved])

  useEffect(() => { if (stepNow) status.current?.focus() }, [stepNow === null])

  const fail = useCallback((error: unknown) => { setResuming(false); setJobId(null); dispatch({ type: 'fail', error }) }, [setJobId])

  const exportStep = useCallback(async (id: number) => {
    try {
      const { text } = await getSubtitleText(id, { fmt: 'srt', field: 'en', includeNotes: false })
      if (!text.trim()) throw new ApiError(404, { code: 'not_found', message: 'There are no English lines to export.' })
      setSrt(text)
      dispatch({ type: 'advance' })
    } catch (e) { fail(e) }
  }, [fail])

  const translateStep = useCallback(async (id: number) => {
    try {
      const config = await getTranslateConfig(id)
      const form = { ...initialForm(config), engine: run.current.engine }
      if (run.current.variant) form.locale = run.current.variant
      const started = await startTranslateRun(id, buildRunBody(form))
      setJobId(started.job_id)
    } catch (e) { fail(e) }
  }, [fail, setJobId])

  const onJobDone = useCallback((job: JobRecord) => {
    if (job.status === 'cancelled' || job.outcome === 'cancelled') { setJobId(null); setNote({ kind: 'cancelled', dramaId: run.current.dramaId }); dispatch({ type: 'reset' }); return }
    if (failed(job)) { fail(new ApiError(0, { code: 'application_error', message: job.error ?? job.message })); return }
    const step = stepForJob(job.job_id)
    const id = Number(job.job_id.split('_').pop())
    setJobId(null)
    dispatch({ type: 'advance' })
    if (step === 'transcribe') void translateStep(id)
    else if (step === 'translate') void exportStep(id)
  }, [fail, setJobId, translateStep, exportStep])

  const { job } = useJob(jobId, { runKey, onDone: onJobDone })

  // A reload mid-run: find the job still running and carry on from its step.
  const ids = flow.phase === 'running' && flow.dramaId !== null && resuming && !jobId ? reattachIds(flow.dramaId) : []
  const seen = useRef({ settled: 0, live: false })
  const attach = useCallback((id: string) => {
    seen.current.live = true
    setResuming(false)
    const step = stepForJob(id)
    if (step) dispatch({ type: 'at', step })
    adopt(id)
  }, [adopt])
  const lookup = useCallback((id: string) => {
    const p = getJob(id)
    void p.then((j) => { if (!['done', 'error', 'cancelled'].includes(j.status) && !j.stale) seen.current.live = true }, () => undefined)
      .finally(() => {
        seen.current.settled += 1
        if (seen.current.settled === reattachIds(0).length && !seen.current.live) fail(lostRun())
      })
    return p
  }, [fail])
  useReattachJob(ids, attach, lookup)

  const start = async () => {
    if (!file) return
    setSrt(null)
    setDownloadError(null)
    setNote(null)
    const abort = new AbortController()
    uploadAbort.current = abort
    let created: number | null = null
    seen.current = { settled: 0, live: false }
    setResuming(false)
    dispatch({ type: 'start' })
    try {
      const d = await createDrama({
        source_language: language, media_type: mediaTypeFor(file.name), title_en: titleFor(file.name, title),
      })
      created = d.id
      dispatch({ type: 'created', dramaId: d.id })
      if (abort.signal.aborted) throw new DOMException('Cancelled', 'AbortError')
      const r = await uploadAndTranscribe(d.id, file, { source_language: language }, false, withSignal(abort.signal))
      dispatch({ type: 'advance' })
      setJobId(r.job_id)
    } catch (e) {
      // Aborting makes fetch reject; that is the user's choice, not a failure.
      if (!abort.signal.aborted) { fail(e); return }
      setResuming(false)
      setNote({ kind: 'cancelled', dramaId: created })
      dispatch({ type: 'reset' })
    } finally { if (uploadAbort.current === abort) uploadAbort.current = null }
  }

  const cancel = () => {
    if (jobId) cancelJob(jobId).then(() => setCancelError(null), setCancelError)
    else uploadAbort.current?.abort()
  }

  const download = async () => {
    if (dramaId === null) return
    setDownloadError(null)
    try {
      const text = srt ?? (await getSubtitleText(dramaId, { fmt: 'srt', field: 'en', includeNotes: false })).text
      downloadText(text, exportFilename(run.current.title, dramaId, 'en', 'srt'), SRT_MIME)
      void markExported(dramaId).catch(() => undefined)
    } catch (e) { setDownloadError(e) }
  }

  const reset = () => { setJobId(null); setFile(null); setTitle(''); setSrt(null); setNote(null); dispatch({ type: 'reset' }) }

  const blocker = blockerText(!!file, blockers, engine)
  const busy = flow.phase === 'running'
  const pct = percentText(job?.progress)

  return (
    <Card title="Make subtitles" meta="File to English SRT in one go." className="make-subtitles" aria-label="Make subtitles">
      {flow.phase === 'idle' || flow.phase === 'error' ? (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (!blocker) void start() }}>
          {/* What went wrong comes before the form it asks the viewer to fix. */}
          {flow.phase === 'error' && (
            <div className="stack">
              <ErrorBanner error={flow.error} />
              <p className="make-subtitles-failed">
                <strong>Failed at {STEP_LABEL[flow.step]}.</strong>{' '}
                {flow.dramaId !== null && (
                  <ButtonLink size="sm" href={routeHref({ name: 'drama', id: flow.dramaId, stage: STEP_STAGE[flow.step] })}>
                    Fix in {STAGE_LABELS[STEP_STAGE[flow.step] as StageId]}
                  </ButtonLink>
                )}
              </p>
            </div>
          )}
          {note && (
            <p className="muted make-subtitles-note" role="status">
              {note.kind === 'cancelled'
                ? (note.dramaId !== null ? 'Cancelled. The title was kept.' : 'Cancelled.')
                : 'The last run stopped before the upload finished.'}{' '}
              {note.dramaId !== null && (
                <ButtonLink size="sm" variant="ghost" href={routeHref({ name: 'drama', id: note.dramaId, stage: null })}>Open title</ButtonLink>
              )}
            </p>
          )}
          <div className="make-subtitles-fields">
            <Field label="Audio or video file">
              <input ref={fileInput} type="file" accept={UPLOAD_EXTENSIONS.join(',')}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </Field>
            <Field label="Source language">
              <select value={LANGUAGES.includes(language) ? language : 'zh'} onChange={(e) => setLanguage(e.target.value)}>
                {LANGUAGES.map((l) => <option key={l} value={l}>{languageLabel(l)}</option>)}
              </select>
            </Field>
            <Field label="Translator">
              <select value={engine} onChange={(e) => setPickedEngine(e.target.value)}>
                {list.map((e) => <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>)}
              </select>
            </Field>
          </div>
          {engine && (
            <PreflightCard needs={['whisper', 'ffmpeg', 'key']} engine={engine}
              onReady={(ok, rows) => { setPreflightOk(ok); setBlockers(rows) }}
              onUseEngine={(name) => setPickedEngine(name)} />
          )}
          {preflightOk && <p className="muted">Ready</p>}
          <Section title="Options" summary={optionsSummary(file ? titleFor(file.name, title) : null, variant)}>
            <div className="make-subtitles-fields">
              <Field label="Title">
                <input value={title} placeholder={file ? titleFor(file.name, '') : ''} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <Field label="English variant">
                <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                  <option value="">Settings default</option>
                  {VARIANTS.map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </Field>
            </div>
          </Section>
          <div className="make-subtitles-go">
            <button type="submit" className={buttonClass('primary')} disabled={!!blocker} aria-describedby="make-subtitles-blocker">
              Make subtitles
            </button>
            {blocker && (
              <p className="stage-blocker" id="make-subtitles-blocker">
                <span>{blocker}</span>
                {!file && <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => fileInput.current?.focus()}>Choose a file</button>}
              </p>
            )}
          </div>
        </form>
      ) : (
        <div className="stack">
          <div ref={status} tabIndex={-1} role="status" aria-live="polite" className="make-subtitles-status">
            {busy ? (
              <>
                <ol className="make-subtitles-steps">
                  {STEPS.map((s: FlowStep) => (
                    <li key={s} aria-current={s === stepNow ? 'step' : undefined}
                      className={STEPS.indexOf(s) < STEPS.indexOf(stepNow!) ? 'is-done' : s === stepNow ? 'is-current' : undefined}>
                      {STEP_LABEL[s]}{s === stepNow && pct && ` ${pct}`}
                    </li>
                  ))}
                </ol>
                {job?.message && <p className="muted">{job.message}</p>}
              </>
            ) : (
              <p><strong>Subtitles are ready.</strong></p>
            )}
          </div>
          <ErrorBanner error={cancelError ?? downloadError} />
          {busy ? (
            (jobId || stepNow === 'upload') && <div className="actions"><button type="button" className={buttonClass('secondary')} onClick={cancel}>Cancel</button></div>
          ) : (
            <div className="make-subtitles-go">
              <button type="button" className={buttonClass('primary')} onClick={() => void download()}>Download SRT</button>
              <div className="actions">
                {flow.phase === 'done' && (
                  <>
                    <ButtonLink variant="ghost" href={routeHref({ name: 'drama', id: flow.dramaId, stage: 'review' })}>Review lines</ButtonLink>
                    <ButtonLink variant="ghost" href={routeHref({ name: 'drama', id: flow.dramaId, stage: null })}>Open title</ButtonLink>
                  </>
                )}
                <button type="button" className={buttonClass('ghost')} onClick={reset}>Make another</button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
