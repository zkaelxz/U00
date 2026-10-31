// Mirrors api/schemas/system.py JobRecord / JobListResponse / JobCancelResult.

type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

const TERMINAL_STATUSES: readonly string[] = ['done', 'error', 'cancelled']

export const isActiveStatus = (status: string | null | undefined) => status === 'queued' || status === 'running'

export const isTerminalStatus = (status: string) => TERMINAL_STATUSES.includes(status)

export interface JobRecord {
  job_id: string
  status: JobStatus | string
  progress: number | null
  message: string
  error: string | null
  description: string | null
  gpu_touching: boolean
  started_at: number | null
  finished_at: number | null
  updated_at: number
  // Redacted, allowlisted projection of the job's result dict.
  result?: Record<string, unknown> | null
  // Normalised outcome of a finished job (null while queued/running).
  outcome?: JobOutcome | null
  outcome_message?: string | null
  // Queued/running on record but not heartbeated for 15 minutes (judged on
  // the server's clock): left behind by a process that died.
  stale?: boolean
  // Running here, but no progress update for a while (advisory only).
  stalled?: boolean
  // Cancel was heard over a minute ago and the worker still runs: when Force
  // stop is offered. Judged on the server's clock; missing counts as false.
  can_force_stop?: boolean
  // The caller started this job or owns its drama, judged by the server from
  // the session. Missing (an older server) counts as false.
  owned_by_me?: boolean
  // The title a drama-scoped job runs on, else null. Missing (an older
  // server) counts as null.
  drama_id?: number | null
  // What the job does; `other` for anything unclassified. Missing counts as other.
  kind?: JobKind
  // The page the job belongs to, decided by the server from the job id; how a
  // job with no title can still link somewhere. Missing counts as null.
  page?: JobPage | null
}

export type JobPage = 'title' | 'sources' | 'discover' | 'live' | 'settings' | 'diagnostics'

export type JobKind =
  | 'transcribe' | 'translate' | 'align' | 'dub' | 'export' | 'review' | 'import' | 'other'

// Packages that install together are named by what they are for; people never chose "ctranslate2".
const INSTALL_GROUP_LABELS: Record<string, string> = {
  faster_whisper: 'Whisper',
  ctranslate2: 'Whisper',
  'nvidia-cublas-cu12': 'Whisper',
  'nvidia-cudnn-cu12': 'Whisper',
  soundfile: 'audio support',
}
const DEPENDENCY_INSTALL_JOB = 'dependency_install'
const INSTALL_DESCRIPTION = /^(Installing|Updating) (\S+)$/

/** The one label for a job in the header menu, the Jobs page and the stage panel. */
export function jobName(j: Pick<JobRecord, 'description' | 'job_id'>): string {
  const text = j.description || j.job_id
  if (j.job_id !== DEPENDENCY_INSTALL_JOB) return text
  const m = INSTALL_DESCRIPTION.exec(text)
  const label = m ? INSTALL_GROUP_LABELS[m[2]] : undefined
  return label ? `${m![1]} ${label}` : text
}

// The server's rule: a member stops only their own jobs (owned_by_me); an
// admin (from away too), the PC owner and auth off stop any (`anyJob`,
// useSession's cancelsAnyJob).
export function offersCancel(job: Pick<JobRecord, 'owned_by_me'>, anyJob: boolean): boolean {
  return anyJob || job.owned_by_me === true
}

export const CANCEL_RULE_NOTE = 'Only the person who started a job, the title’s owner or an admin can cancel it.'

// Mirrors api/schemas/system.py JobQueueSummary: counts for everyone, places
// only for the caller's own waiting jobs.
export interface JobQueueSummary {
  lane: 'gpu'
  running: number
  waiting: number
  mine: { job_id: string; position: number }[]
}

export function queueLine(s: Pick<JobQueueSummary, 'running' | 'waiting'>): string {
  return `${s.running} ${s.running === 1 ? 'job' : 'jobs'} running (all lanes), ${s.waiting} waiting in the GPU queue`
}

export const queuePlace = (position: number) => `Waiting: #${position} in the GPU queue`

type JobOutcome = 'ok' | 'failed' | 'cancelled' | 'interrupted' | 'partial' | 'kept_existing'

// A terminal job that did not fully succeed, even when status is 'done'
// (a transcription that returned early with a failure reason, say).
export function jobFailed(job: Pick<JobRecord, 'status' | 'outcome'>): boolean {
  return job.status === 'error' || job.outcome === 'failed' || job.outcome === 'cancelled' || job.outcome === 'interrupted'
}

// The status a person should read. The server calls a job `done` when its
// worker returned, even when the work failed (a missing model, say); the
// outcome says what happened, so a done job with a failed or cancelled
// outcome reads as Failed or Cancelled in every list, pill and filter.
export function shownStatus(job: Pick<JobRecord, 'status' | 'outcome'>): string {
  if (job.status !== 'done' || !jobFailed(job)) return job.status
  return job.outcome === 'cancelled' ? 'cancelled' : 'error'
}

// Done and not failed/cancelled: safe to treat the job's output as ready.
export function jobSucceeded(job: Pick<JobRecord, 'status' | 'outcome'> | null | undefined): boolean {
  return !!job && job.status === 'done' && !jobFailed(job)
}

const OUTCOME_LABELS: Record<JobOutcome, string> = {
  ok: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted: Baihe restarted',
  partial: 'Finished with problems',
  kept_existing: 'Nothing new; existing lines kept',
}

const bare = (text: string) => text.trim().replace(/[.\s]+$/, '').toLowerCase()

// Plain-words outcome line for a finished job, or null when there is none.
// A message that only repeats the label ("Finished" / "Finished.") shows once.
export function jobOutcomeText(job: Pick<JobRecord, 'outcome' | 'outcome_message'>): string | null {
  if (!job.outcome) return null
  const label = OUTCOME_LABELS[job.outcome] ?? job.outcome
  const message = job.outcome_message?.trim()
  if (!message) return label
  // The stored reason already opens with this label ("Interrupted: Baihe restarted while…").
  if (job.outcome === 'interrupted' && bare(message).startsWith(bare(label))) return message
  return bare(message) === bare(label) ? message : `${label}: ${message}`
}

// The line under a status pill. The pill already says Failed or Cancelled,
// so those show the reason alone; a plain "Finished" adds nothing.
export function jobOutcomeDetail(job: Pick<JobRecord, 'status' | 'outcome' | 'outcome_message'>): string | null {
  if (!job.outcome) return null
  const message = job.outcome_message?.trim() || null
  if (job.outcome === 'failed' || job.outcome === 'cancelled') return message && bare(message) !== bare(OUTCOME_LABELS[job.outcome]) ? message : null
  // The pill says Cancelled (the stored status), so the line says why.
  if (job.outcome === 'interrupted') return message ?? OUTCOME_LABELS.interrupted
  if (job.outcome === 'ok') return message && bare(message) !== bare(OUTCOME_LABELS.ok) ? message : null
  return jobOutcomeText(job)
}

export interface JobListResponse {
  items: JobRecord[]
  count: number
}

export interface JobForceStopResult {
  job_id: string
  force_stopped: boolean
  status: string
  worker_still_running: boolean
}

export const FORCE_STOP_HINT =
  'Cancel was sent but the job has not stopped. Force stop ends it here; its worker keeps running in the background until it finishes, and a hosted AI request already sent keeps running and is still billed.'

const AT_ONCE = 'stops at once'
const AFTER_AI = 'stops after the current AI request (usually under a minute, up to 5; an AI request already sent is still billed)'
const AFTER_BATCH = 'stops after the current batch'
const AFTER_STEP = 'stops after the current step'

// How fast Cancel takes effect, by job-id prefix. Process jobs and ffmpeg
// are killed; hosted AI calls cannot be interrupted mid-request (SDK timeout
// 300 s, Ollama up to 900 s) and are billed even when the result is dropped.
// Ids with no entry get the generic step wording.
const CANCEL_HINTS: [prefix: string, hint: string][] = [
  ['transcribe_', AT_ONCE], ['retranscribe_', AT_ONCE], ['diarize_', AT_ONCE], ['resegment_', AT_ONCE],
  ['autotune_', AT_ONCE], ['dub_', AT_ONCE], ['extract_audio_', AT_ONCE], ['burned_video_', AT_ONCE],
  ['softsub_video_', AT_ONCE], ['dubbed_video_', AT_ONCE], ['burnpreview_', AT_ONCE],
  ['translate_', AFTER_AI], ['novel_glossary_', AFTER_AI], ['lines_glossary_', AFTER_AI],
  ['spelling_suggest_', AFTER_AI], ['flag_', AFTER_AI], ['fixflag_', AFTER_AI], ['consistency_', AFTER_AI],
  ['emotion_', AFTER_AI], ['notes_', AFTER_AI],
  ['bulk_', AFTER_BATCH],
]

const upperFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** The Cancel button's tooltip: "Cancel: stops at once." for a job id. */
export function cancelHint(jobId: string | null | undefined): string {
  const hint = CANCEL_HINTS.find(([prefix]) => (jobId ?? '').startsWith(prefix))?.[1] ?? AFTER_STEP
  return `Cancel: ${hint}.`
}

/** The job line while a cancel is pending: the server's "Cancelling..." with this kind's wait. */
export function cancellingText(jobId: string | null | undefined): string {
  return `Cancelling... ${upperFirst(cancelHint(jobId).slice('Cancel: '.length))}`
}

export const isCancellingMessage = (message: string | null | undefined) => !!message && message.startsWith('Cancelling...')

// A running transcription, or any job past 10%, is hours of GPU time or paid
// calls: Cancel asks first. Queued and just-started jobs lose nothing.
export function cancelNeedsConfirm(job: Pick<JobRecord, 'status' | 'kind' | 'progress'>): boolean {
  return job.status === 'running' && (job.kind === 'transcribe' || (job.progress ?? 0) > 0.1)
}

// Force stop shares Cancel's permission, so it follows the same rule.
export function offersForceStop(job: Pick<JobRecord, 'status' | 'can_force_stop' | 'owned_by_me'>, anyJob: boolean): boolean {
  return job.status === 'running' && job.can_force_stop === true && offersCancel(job, anyJob)
}

export interface JobCancelResult {
  job_id: string
  cancel_requested: boolean
  status: string
}

// Mirrors api/job_stage_schemas.py JobStageTimings (GET /api/jobs/{id}/stages).
interface JobStage {
  stage: string
  started_at: number
  duration_seconds: number
  cost_usd: number
}

export interface JobStageRun {
  run_started_at: number
  running: boolean
  total_seconds: number
  cost_usd: number
  stages: JobStage[]
}

// Newest run first, at most 10; empty for a job recorded before stage timing.
export interface JobStageTimings {
  job_id: string
  runs: JobStageRun[]
}
