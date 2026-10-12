// Pure helpers for the Queue card on the Jobs page (JobQueue.tsx).
import { humanize, type BadgeTone } from '../../components/labels'
import type { QueueItem, QueueItemStatus, QueueSchedule, QueueSettingRow, QueueState, QueueStep } from '../../types/queue'
import { STAGE_LABELS, type StageId } from '../workspace/stages'

export const STEP_LABELS: Record<QueueStep, string> = {
  transcribe: 'Transcribe',
  translate: 'Translate',
  export: 'Export subtitles',
}

export const STEP_HELP: Record<QueueStep, string> = {
  transcribe: "Uses the title's saved Transcribe options. Replaces its current lines; the old ones stay in line History.",
  translate: "Uses the title's saved Translate options and engine.",
  export: 'Saves English subtitles (SRT) as the title\'s subtitle download.',
}

const STATUS: Record<QueueItemStatus, { label: string; tone: BadgeTone }> = {
  waiting: { label: 'Waiting', tone: 'neutral' },
  running: { label: 'Running', tone: 'info' },
  done: { label: 'Done', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  skipped: { label: 'Skipped', tone: 'warn' },
  interrupted: { label: 'Interrupted', tone: 'warn' },
  cancelled: { label: 'Cancelled', tone: 'warn' },
}

export const statusLabel = (s: QueueItemStatus) => STATUS[s]?.label ?? s
export const statusTone = (s: QueueItemStatus): BadgeTone => STATUS[s]?.tone ?? 'neutral'

const FINISHED: QueueItemStatus[] = ['done', 'failed', 'skipped', 'interrupted', 'cancelled']
const RETRYABLE: QueueItemStatus[] = ['failed', 'skipped', 'interrupted', 'cancelled']

export const isFinished = (i: QueueItem) => FINISHED.includes(i.status)
export const canRetry = (i: QueueItem) => i.mine && RETRYABLE.includes(i.status)
export const canRemove = (i: QueueItem) => i.mine && i.status !== 'running'
export const finishedCount = (items: QueueItem[]) => items.filter((i) => i.mine && isFinished(i)).length
export const waitingCount = (items: QueueItem[]) => items.filter((i) => i.status === 'waiting').length

export function itemName(i: QueueItem): string {
  return `${STEP_LABELS[i.step]} ${i.title}`
}

export function runStateText(q: QueueState): string {
  if (q.state === 'running') return 'Running: one item at a time.'
  if (q.state === 'paused') return 'Paused: no new item starts. A running one finishes.'
  return waitingCount(q.items) ? 'Not running.' : 'Nothing waiting.'
}

export function scheduleText(s: QueueSchedule | null, now: Date = new Date()): string | null {
  if (!s) return null
  if (s.missed) return `Missed: the start at ${s.time} passed while Baihe was off. Start now?`
  const at = new Date(s.start_at * 1000)
  const sameDay = at.toDateString() === now.toDateString()
  return `Starts once at ${s.time} ${sameDay ? 'today' : 'tomorrow'}, on this PC's clock.`
}

export const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/

// The plain cost line next to a Translate item: which engine, and that it
// spends money on the owner's key, capped by the Spending settings.
export function translateCostNote(engineLabel: string, paid: boolean, monthlyCap: number): string {
  // Engine descriptions end with a full stop; the sentence supplies its own.
  engineLabel = engineLabel.replace(/\.+$/, '')
  if (!paid) return `Uses ${engineLabel}, which is free.`
  const cap = monthlyCap > 0
    ? `It stops at your $${monthlyCap.toFixed(2)} monthly spending cap.`
    : 'No monthly spending cap is set, so the queue will skip it: set one in Settings > Spending.'
  return `Uses ${engineLabel}. Uses your API key and costs money. ${cap}`
}

const STEP_STAGE: Record<QueueStep, StageId> = { transcribe: 'source', translate: 'translate', export: 'export' }

/** The title stage where a step's options are changed. */
export const settingsStage = (step: QueueStep): { stage: StageId; label: string } => ({
  stage: STEP_STAGE[step],
  label: STAGE_LABELS[STEP_STAGE[step]],
})

/** A saved-settings row's value; the engine row names the engine in words. */
export function savedSettingValue(row: QueueSettingRow): string {
  if (!row.engine) return row.value
  const engine = humanize('engine', row.engine)
  return row.value ? `${engine}, ${row.value}` : engine
}
