/** The file run behind the Add box: its choices, the run itself and its resume after a reload. */
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import { ApiError, uploadProgressFetch, withSignal } from '../../api/client'
import { getSubtitleText, markExported } from '../../api/export'
import { getSettings } from '../../api/settings'
import { cancelJob, probeJob } from '../../api/jobs'
import { createDrama } from '../../api/library'
import { getFileRun, startFileRun } from '../../api/queue'
import { translateApi } from '../../api/translate'
import { uploadAndTranscribe } from '../../api/workspace'
import type { UploadSent } from '../../components/UploadProgress'
import { downloadText } from '../../components/downloadBlob'
import { JOB_FAILED } from '../../components/errorMessages'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useLatest } from '../../hooks/useLatest'
import { useLoad } from '../../hooks/useLoad'
import { usePersistedState } from '../../hooks/usePersistedState'
import { useReattachJob } from '../../hooks/useReattachJob'
import { isTerminalStatus, type JobRecord } from '../../types/jobs'
import type { PreflightRow } from '../preflight/preflightModel'
import { checkUploadFile } from '../workspace/sourceForm'
import { exportFilename, srtFileText } from '../workspace/exportForm'
import type { FileRunRequest } from './addBoxModel'
import {
  IDLE, blockerText, finishedStepJob, flowReducer, mediaTypeFor, parseSaved, reattachIds, savedFor, stepForJob, titleFor, type FlowStep,
} from './makeSubtitlesFlow'

export const LANGUAGES = ['zh', 'ja', 'ko', 'id', 'th']
const SRT_MIME = 'application/x-subrip'

// A transcription that found no lines ends "done" with a failed_reason; translating it would only be refused.
const failed = (job: JobRecord) => job.status === 'error' || job.outcome === 'failed' || !!job.result?.failed_reason
const RUN_POLL_MS = 2000
const lostRun = () =>
  new ApiError(404, { code: 'not_found', message: 'Lost track of this run while the page was closed. Open the title to see how far it got.' })

/**
 * File to English SRT in one go: the choices, the run and its resume after a reload. The stage stepper
 * stays the way to fix a step that went wrong: a failure links to the stage that owns it.
 */
export function useFileRun() {
  const [flow, dispatch] = useReducer(flowReducer, IDLE)
  const [saved, setSaved] = usePersistedState<unknown>('makeSubtitles.run', null)
  const [language, setLanguage] = usePersistedState('makeSubtitles.language', 'zh')
  // The box remembers the translator for the next file, like the language.
  const [pickedEngine, setPickedEngine] = usePersistedState('makeSubtitles.engine', '')
  const [file, setFile] = useState<File | null>(null)
  const [sent, setSent] = useState<UploadSent | null>(null)
  const [title, setTitle] = useState('')
  const [variant, setVariant] = useState('')
  const [blockers, setBlockers] = useState<PreflightRow[]>([])
  const [jobId, setJobId, runKey, adopt] = useJobRun()
  const [srt, setSrt] = useState<string | null>(null)
  // True only for a run found in storage: a fresh run has no earlier job to look for.
  const [resuming, setResuming] = useState(false)
  const [downloadError, setDownloadError] = useState<unknown>(null)
  const [cancelError, setCancelError] = useState<unknown>(null)
  // Why the form is showing again with a title already in the Library (null dramaId: nothing was created).
  const [note, setNote] = useState<{ kind: 'cancelled' | 'upload-lost'; dramaId: number | null } | null>(null)
  const uploadAbort = useRef<AbortController | null>(null)

  const [enginesTry, setEnginesTry] = useState(0)
  const engines = useLoad(translateApi.engineList, enginesTry)
  // Only labels the variant option; a person who cannot read Settings just sees the generic wording.
  const settings = useLoad(getSettings, 0)
  const defaultLocale = settings.data?.preferences.default_locale
  const list = engines.data?.items ?? []
  const defaultEngine = list.find((e) => e.name === engines.data?.default_engine)?.name ?? list[0]?.name ?? ''
  // A remembered translator the list no longer offers falls back to the default; before the list loads it stands.
  const engine = pickedEngine && (!list.length || list.some((e) => e.name === pickedEngine)) ? pickedEngine : defaultEngine

  // Read inside job callbacks, which outlive the render that created them.
  const run = useRef<{ title: string; dramaId: number | null }>({ title: '', dramaId: null })
  const dramaId = flow.phase === 'idle' ? null : flow.dramaId
  useEffect(() => { run.current = { title: file ? titleFor(file.name, title) : title, dramaId } })
  const stepNow = flow.phase === 'running' ? flow.step : null

  // A ref, not a dep: setSaved is a fresh function each render.
  const setSavedRef = useLatest(setSaved)
  useEffect(() => { setSavedRef.current(savedFor(flow)) }, [flow, setSavedRef])

  const fail = useCallback((error: unknown) => { setResuming(false); setJobId(null); dispatch({ type: 'fail', error }) }, [setJobId])

  const exportStep = useCallback(async (id: number) => {
    try {
      const { text } = await getSubtitleText(id, { fmt: 'srt', field: 'en', includeNotes: false })
      if (!text.trim()) throw new ApiError(404, { code: 'not_found', message: 'There are no English lines to export.' })
      setSrt(text)
      dispatch({ type: 'advance' })
    } catch (e) { fail(e) }
  }, [fail])

  // The PC queue starts the translation once the transcription ends, with the box's choices sent at the
  // start, so a run carries on with the page closed. This waits for that job, or for the run's end.
  const translateStep = useCallback(async (id: number) => {
    try {
      for (;;) {
        const r = await getFileRun(id)
        if (r.state === 'done') { void exportStep(id); return }
        if (r.state === 'stopped') throw new ApiError(0, { code: JOB_FAILED, message: r.reason || 'The run stopped.' })
        if (r.state === 'idle') throw lostRun()
        if (r.step === 'run_translate' && r.job_id) { setJobId(r.job_id); return }
        await new Promise((resolve) => setTimeout(resolve, RUN_POLL_MS))
      }
    } catch (e) { fail(e) }
  }, [fail, setJobId, exportStep])

  const onJobDone = useCallback((job: JobRecord) => {
    if (job.status === 'cancelled' || job.outcome === 'cancelled') { setJobId(null); setNote({ kind: 'cancelled', dramaId: run.current.dramaId }); dispatch({ type: 'reset' }); return }
    if (failed(job)) { fail(new ApiError(0, { code: JOB_FAILED, message: job.outcome_message || job.error || job.message })); return }
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
  const seen = useRef({ settled: 0, live: false, finished: new Map<string, JobRecord>() })
  // The saved run this page picked up, so a step that ended while the page was closed can carry on.
  const resumedFrom = useRef<{ dramaId: number; step: FlowStep } | null>(null)
  const attach = useCallback((id: string) => {
    seen.current.live = true
    setResuming(false)
    const step = stepForJob(id)
    if (step) dispatch({ type: 'at', step })
    adopt(id)
  }, [adopt])
  const lookup = useCallback((id: string) => {
    const p = probeJob(id)
    void p.then((j) => {
      if (!j || j.stale) return
      if (isTerminalStatus(j.status)) seen.current.finished.set(id, j)
      else seen.current.live = true
    }, () => undefined)
      .finally(() => {
        seen.current.settled += 1
        if (seen.current.settled !== reattachIds(0).length || seen.current.live) return
        // Nothing still running: the step may have ended while the page was closed.
        const from = resumedFrom.current
        if (from?.step === 'export') { setResuming(false); void exportStep(from.dramaId); return }
        const ended = from && finishedStepJob(from.step, from.dramaId, seen.current.finished)
        if (ended) { setResuming(false); onJobDone(ended) } else fail(lostRun())
      })
    return p
  }, [fail, onJobDone, exportStep])
  useReattachJob(ids, attach, lookup)

  // Checked before the title exists, so a wrong or empty file leaves nothing behind.
  // The size limit reaches this page only with a title's media info, so the upload checks size.
  const refused = (f: File): boolean => {
    const problem = checkUploadFile(f.name, f.size, Infinity)
    if (!problem) return false
    dispatch({ type: 'start' })
    fail(new ApiError(422, { code: 'validation_error', message: problem }))
    return true
  }

  const start = async (r: FileRunRequest) => {
    setSrt(null)
    setDownloadError(null)
    setNote(null)
    if (refused(r.file)) return
    const abort = new AbortController()
    uploadAbort.current = abort
    let created: number | null = null
    seen.current = { settled: 0, live: false, finished: new Map() }
    resumedFrom.current = null
    setResuming(false)
    setSent(null)
    dispatch({ type: 'start' })
    try {
      const d = await createDrama({
        source_language: r.language, media_type: mediaTypeFor(r.file.name), title_en: titleFor(r.file.name, r.title),
      })
      created = d.id
      dispatch({ type: 'created', dramaId: d.id })
      if (abort.signal.aborted) throw new DOMException('Cancelled', 'AbortError')
      const u = await uploadAndTranscribe(d.id, r.file, { source_language: r.language }, false,
        withSignal(abort.signal, uploadProgressFetch((loaded, total) => setSent({ loaded, total }))))
      await startFileRun(d.id, { job_id: u.job_id, engine: r.engine || engine, locale: r.variant || null })
      dispatch({ type: 'advance' })
      setJobId(u.job_id)
    } catch (e) {
      // Aborting makes fetch reject; that is the user's choice, not a failure.
      if (!abort.signal.aborted) { fail(e); return }
      setResuming(false)
      setNote({ kind: 'cancelled', dramaId: created })
      dispatch({ type: 'reset' })
    } finally { if (uploadAbort.current === abort) uploadAbort.current = null }
  }

  // Once, after the first render: a run saved in storage is followed again.
  const resumed = useRef(false)
  useEffect(() => {
    if (resumed.current) return
    resumed.current = true
    const s = parseSaved(saved)
    // A run that died before the upload finished has nothing to follow.
    // eslint-disable-next-line react/set-state-in-effect -- the resumed run must start from the first render's choices, and the guard above makes this a single pass
    if (s && s.step !== 'upload') { resumedFrom.current = s; setResuming(true); dispatch({ type: 'resume', saved: s }); return }
    if (s) setNote({ kind: 'upload-lost', dramaId: s.dramaId })
  }, [saved])

  const cancel = () => {
    if (jobId) cancelJob(jobId).then(() => setCancelError(null), setCancelError)
    else uploadAbort.current?.abort()
  }

  const download = async () => {
    if (dramaId === null) return
    setDownloadError(null)
    try {
      const text = srt ?? (await getSubtitleText(dramaId, { fmt: 'srt', field: 'en', includeNotes: false })).text
      downloadText(srtFileText(text), exportFilename(run.current.title, dramaId, 'en', 'srt'), SRT_MIME)
      void markExported(dramaId).catch(() => undefined)
    } catch (e) { setDownloadError(e) }
  }

  const reset = () => { setJobId(null); setFile(null); setTitle(''); setSrt(null); setNote(null); dispatch({ type: 'reset' }) }

  const choices = (f: File): FileRunRequest => ({ file: f, engine, language, title, variant })

  return {
    flow, file, setFile, language, setLanguage, engine, setPickedEngine, list, engines, retryEngines: () => setEnginesTry((n) => n + 1),
    title, setTitle, variant, setVariant, defaultLocale, setBlockers, blocker: blockerText(!!file, blockers, engine),
    job, jobId, stepNow, sent, note, cancelError, downloadError, srt,
    start, refused, choices, cancel, download, reset,
    /** The run's progress or its result, which takes the box's place on the Add page. */
    showingRun: flow.phase === 'running' || flow.phase === 'done',
  }
}

export type FileRun = ReturnType<typeof useFileRun>
