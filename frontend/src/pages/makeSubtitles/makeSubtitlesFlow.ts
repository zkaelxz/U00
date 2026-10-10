// The one-screen "Make subtitles" run: which step it is on, what to resume
// after a reload, and the wording of what is still needed. Pure of React.
import { engineShortName } from '../../api/translate'
import type { PreflightRow } from '../preflight/preflightModel'
import { isVideoFile } from '../workspace/sourceForm'

export const STEPS = ['upload', 'transcribe', 'translate', 'export'] as const
export type FlowStep = (typeof STEPS)[number]

export const STEP_LABEL: Record<FlowStep, string> = {
  upload: 'Upload', transcribe: 'Transcribe', translate: 'Translate', export: 'Export',
}

/** The workspace stage that fixes each step (the stage stepper stays the fix-it view). */
export const STEP_STAGE: Record<FlowStep, string> = {
  upload: 'source', transcribe: 'source', translate: 'translate', export: 'export',
}

export type Flow =
  | { phase: 'idle' }
  // dramaId is null until createDrama answers.
  | { phase: 'running'; dramaId: number | null; step: FlowStep }
  | { phase: 'error'; dramaId: number | null; step: FlowStep; error: unknown }
  | { phase: 'done'; dramaId: number }

export type FlowAction =
  | { type: 'start' }
  | { type: 'created'; dramaId: number }
  | { type: 'advance' }
  | { type: 'at'; step: FlowStep }
  | { type: 'fail'; error: unknown }
  | { type: 'resume'; saved: SavedRun | null }
  | { type: 'reset' }

/** What survives a reload: the title being made and the step it was on. */
export interface SavedRun {
  dramaId: number
  step: FlowStep
}

export const IDLE: Flow = { phase: 'idle' }

const isStep = (v: unknown): v is FlowStep => STEPS.includes(v as FlowStep)

/** Reads a stored value back; anything malformed means "nothing to resume". */
export function parseSaved(raw: unknown): SavedRun | null {
  const r = raw as Partial<SavedRun> | null
  return r && Number.isInteger(r.dramaId) && (r.dramaId as number) > 0 && isStep(r.step)
    ? { dramaId: r.dramaId as number, step: r.step }
    : null
}

/** What to store for a flow: only a run in progress is worth resuming. */
export function savedFor(flow: Flow): SavedRun | null {
  return flow.phase === 'running' && flow.dramaId !== null ? { dramaId: flow.dramaId, step: flow.step } : null
}

const nextStep = (s: FlowStep): FlowStep | null => STEPS[STEPS.indexOf(s) + 1] ?? null

export function flowReducer(flow: Flow, a: FlowAction): Flow {
  switch (a.type) {
    case 'start':
      return { phase: 'running', dramaId: null, step: 'upload' }
    case 'created':
      return flow.phase === 'running' ? { ...flow, dramaId: a.dramaId } : flow
    case 'advance': {
      if (flow.phase !== 'running') return flow
      const next = nextStep(flow.step)
      if (next) return { ...flow, step: next }
      return flow.dramaId === null ? flow : { phase: 'done', dramaId: flow.dramaId }
    }
    case 'at':
      return flow.phase === 'running' ? { ...flow, step: a.step } : flow
    case 'fail':
      if (flow.phase !== 'running') return flow
      return { phase: 'error', dramaId: flow.dramaId, step: flow.step, error: a.error }
    case 'resume':
      return flow.phase === 'idle' && a.saved ? { phase: 'running', ...a.saved } : flow
    case 'reset':
      return IDLE
  }
}

/** Which step a job id belongs to; the video extraction job also follows the transcribe run. */
export function stepForJob(jobId: string): FlowStep | null {
  if (/^(extract_audio|transcribe)_\d+$/.test(jobId)) return 'transcribe'
  if (/^translate_\d+$/.test(jobId)) return 'translate'
  return null
}

/** Jobs a reload may find still running for a title, in pipeline order. */
export const reattachIds = (dramaId: number): string[] =>
  [`extract_audio_${dramaId}`, `transcribe_${dramaId}`, `translate_${dramaId}`]

export const mediaTypeFor = (filename: string): 'video_drama' | 'audio_drama' =>
  isVideoFile(filename) ? 'video_drama' : 'audio_drama'

/** The file name without its extension: the title when none is typed. */
export function titleStem(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return (dot > 0 ? filename.slice(0, dot) : filename).trim()
}

export const titleFor = (filename: string, typed: string): string => typed.trim() || titleStem(filename)

// In Latin-letter widths; a CJK or Hangul character takes two.
const SUMMARY_TITLE_WIDTH = 24
const WIDE = /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/u

/** The closed Options line. A file name can be hundreds of characters with no spaces, which would
 *  wrap the one-line summary into a paragraph, so the title is clipped here (the field keeps it whole). */
export function optionsSummary(title: string | null, variant: string): string {
  return `title ${title === null ? 'from the file name' : clipToWidth(title, SUMMARY_TITLE_WIDTH)}, ${variant || 'default'} English`
}

function clipToWidth(text: string, width: number): string {
  const chars = Array.from(text)
  const widthOf = (c: string) => (WIDE.test(c) ? 2 : 1)
  if (chars.reduce((n, c) => n + widthOf(c), 0) <= width) return text
  let used = 1 // the ellipsis
  let out = ''
  for (const c of chars) {
    if (used + widthOf(c) > width) break
    used += widthOf(c)
    out += c
  }
  return `${out}…`
}

const BLOCKER: Record<string, (engine: string) => string> = {
  whisper: () => 'install Whisper',
  ffmpeg: () => 'install FFmpeg',
  key: (engine) => `add a ${engineShortName({ name: engine })} key`,
}

/** The one-line reason the primary is disabled, or null when it can run. */
export function blockerText(hasFile: boolean, blockers: PreflightRow[], engine: string): string | null {
  if (!hasFile) return 'Still needed: an audio or video file.'
  const first = blockers.find((b) => b.blocking)
  return first ? `Still needed: ${(BLOCKER[first.need] ?? (() => first.label))(engine)}.` : null
}

/** The percent to show for a job's progress (0..1), or null before it reports one. */
export const percentText = (progress: number | null | undefined): string | null =>
  typeof progress === 'number' ? `${Math.round(Math.min(1, Math.max(0, progress)) * 100)}%` : null
