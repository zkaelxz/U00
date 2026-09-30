// Pure helpers for the automatic backup UI: the Settings "Automatic backups"
// Card (settings/AutoBackupCard.tsx) and the Library tools snapshot block
// (libraryAdmin/SnapshotBlock.tsx).
import { ApiError } from '../api/client'
import { describeError, safeDetail } from '../components/errorMessages'
import type {
  AutoBackupSettings,
  AutoBackupSettingsUpdate,
  BackupFrequency,
  RestoreDramaDone,
  SnapshotDrama,
  SnapshotInfo,
  SnapshotKind,
} from '../types/backups'
import { formatBytes } from './libraryAdmin/libraryAdmin'

export const FREQUENCY_OPTIONS: readonly [BackupFrequency, string][] = [
  ['daily', 'Daily'],
  ['weekly', 'Weekly'],
  ['monthly', 'Monthly'],
]

export const ONE_SNAPSHOT_NOTE = 'Keeps one snapshot; each new backup replaces it.'
export const DEFAULT_FOLDER_TEXT = 'Library backups folder (default)'

type DateOpts = { locale?: string; timeZone?: string }

/** An ISO timestamp as local date and time ("30 Sep 2026, 14:05"), or null. */
export function formatWhen(iso: string | null | undefined, opts: DateOpts = {}): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(opts.locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: opts.timeZone })
}

export function snapshotKindLabel(kind: SnapshotKind | null | undefined): string {
  return kind === 'full' ? 'Database + media' : 'Database only'
}

const plural = (n: number, one: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : `${one}s`}`

/** Kind, size and drama count ("Database only · 12.3 MB · 3 dramas"). */
export function snapshotFacts(s: SnapshotInfo): string {
  const parts = [snapshotKindLabel(s.kind)]
  if (typeof s.size === 'number') parts.push(formatBytes(s.size))
  if (typeof s.drama_count === 'number') parts.push(plural(s.drama_count, 'drama'))
  return parts.join(' · ')
}

/** One line for the current snapshot, for both places that show it. */
export function describeSnapshot(s: SnapshotInfo | null, opts: DateOpts = {}): string {
  if (!s) return 'Checking for a snapshot…'
  if (!s.exists) return 'No snapshot yet.'
  if (s.readable === false) return "A snapshot file is there, but it can't be read. Back up again to replace it."
  const when = formatWhen(s.created_at, opts)
  return `${when ? `${when} · ` : ''}${snapshotFacts(s)}`
}

/** The Card's one-line meta: "Off" or "Weekly · database only". */
export function settingsSummary(s: AutoBackupSettings): string {
  if (!s.enabled) return 'Off'
  const freq = FREQUENCY_OPTIONS.find(([v]) => v === s.frequency)?.[1] ?? 'On'
  return `${freq} · ${s.include_media ? 'with media' : 'database only'}`
}

/**
 * When the next automatic backup runs. The server checks once an hour while
 * Baihe is running, so a time already past reads "due now". Null when off.
 */
export function nextRunText(s: AutoBackupSettings, now: Date = new Date(), opts: DateOpts = {}): string | null {
  if (!s.enabled || !s.next_run_at) return null
  const at = new Date(s.next_run_at)
  if (Number.isNaN(at.getTime())) return null
  if (at.getTime() <= now.getTime()) return 'Next backup: due now (within the hour while Baihe is running).'
  return `Next backup: ${formatWhen(s.next_run_at, opts)}.`
}

/**
 * "Back up now" as a small state machine. Pressed with no snapshot: start
 * (replace: false). Pressed with a snapshot: ask first. Pressed again while
 * asking: start with replace: true. Unknown snapshot (still loading or
 * failed): start with replace: false; the server refuses with a 422 when one
 * exists, and the caller reloads the snapshot and asks (see needsReplaceConfirm).
 */
export type BackupNowStep = { kind: 'confirm' } | { kind: 'start'; replace: boolean }

export function backupNowStep(snapshot: SnapshotInfo | null, confirming: boolean): BackupNowStep {
  if (confirming) return { kind: 'start', replace: true }
  if (snapshot?.exists) return { kind: 'confirm' }
  return { kind: 'start', replace: false }
}

/** The server said a snapshot exists and replace was not sent. */
export function needsReplaceConfirm(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && e.code === 'invalid_input' && /already exists/i.test(e.message)
}

/** What the replace confirmation says about the snapshot about to go. */
export function replaceWarning(s: SnapshotInfo | null, opts: DateOpts = {}): string {
  if (!s?.exists) return 'This replaces the current snapshot.'
  const when = formatWhen(s.created_at, opts)
  const which = when ? `the snapshot from ${when}` : 'the current snapshot'
  return `This replaces ${which} (${snapshotFacts(s)}). Only one snapshot is kept.`
}

/**
 * The folder field: the value to send when it changed, or null when it did
 * not. Surrounding spaces are dropped; empty means the default folder.
 */
export function folderChange(draft: string, saved: string): string | null {
  const next = draft.trim()
  return next === saved.trim() ? null : next
}

/** Only the fields whose value differs from the saved settings. */
export function changedSettings(saved: AutoBackupSettings, next: AutoBackupSettingsUpdate): AutoBackupSettingsUpdate {
  const out: AutoBackupSettingsUpdate = {}
  if (next.enabled !== undefined && next.enabled !== saved.enabled) out.enabled = next.enabled
  if (next.frequency !== undefined && next.frequency !== saved.frequency) out.frequency = next.frequency
  if (next.include_media !== undefined && next.include_media !== saved.include_media) out.include_media = next.include_media
  if (next.folder !== undefined && next.folder !== saved.folder) out.folder = next.folder
  return out
}

// Said instead of a refused-folder message that names a path (safeDetail drops those).
export const FOLDER_RULES =
  'Use a full folder path that already exists and is outside the library (for example D:\\Backups), or leave it empty.'

/**
 * The server's own sentence for a refused setting, unless it names a path or
 * key (then `fallback`, if given), else the plain text for the error code.
 */
export function serverSentence(e: unknown, fallback?: string): string {
  if (e instanceof ApiError && e.status !== 403 && (e.code === 'invalid_input' || e.code === 'validation_error')) {
    const detail = safeDetail(e.message)
    if (detail) return detail
    if (fallback) return fallback
  }
  return describeError(e, { pcOnly: true, serverText: true }).title
}

/** Case-insensitive title filter for the restore picker. */
export function filterSnapshotDramas(dramas: readonly SnapshotDrama[], query: string): SnapshotDrama[] {
  const q = query.trim().toLocaleLowerCase()
  if (!q) return [...dramas]
  return dramas.filter((d) => d.title.toLocaleLowerCase().includes(q) || String(d.id) === q)
}

/** Today as YYYY-MM-DD, the date the server adds to a copy's title. */
export function isoDay(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
}

/** The notes shown before the RESTORE confirm. */
export function restoreNotes(drama: SnapshotDrama, kind: SnapshotKind, today: string = isoDay()): string[] {
  const notes: string[] = []
  if (drama.exists_now) {
    notes.push(`Still in your library — it will be restored as a new copy titled '${drama.title} (restored ${today})'.`)
  } else {
    notes.push('It comes back as it was when the snapshot was made.')
  }
  if (kind === 'db-only') {
    notes.push('Files (audio/video/pages) are not in this snapshot; only the text and settings come back.')
  }
  return notes
}

// Known skipped tables, in plain words (anything else is left out).
const SKIPPED_WORDS: Record<string, string> = {
  usage_log: 'spending history',
  bulk_jobs: 'provider batch jobs',
  metadata_research_results: 'cached online research',
}

/** The result line after a restore. */
export function describeRestore(r: RestoreDramaDone): string {
  const lines = r.counts?.lines
  const parts = [
    r.restored_as_new ? `Restored '${r.title}' as a new drama.` : `Restored '${r.title}'.`,
  ]
  if (typeof lines === 'number') parts.push(`${plural(lines, 'line')}.`)
  if (!r.media_restored) {
    parts.push(r.snapshot_kind === 'db-only' ? 'No files were in the snapshot.' : 'No files were restored.')
  }
  if (r.series === 'recreated') parts.push('Its series was gone, so it was restored from the snapshot too.')
  if (r.series === 'dropped_private') {
    parts.push("Its series is now someone else's private series, so the drama is back without a series.")
  }
  const skipped = r.skipped_tables.map((t) => SKIPPED_WORDS[t]).filter(Boolean)
  if (skipped.length) {
    const list = skipped.length > 1 ? `${skipped.slice(0, -1).join(', ')} and ${skipped[skipped.length - 1]}` : skipped[0]
    parts.push(`Not restored: ${list}.`)
  }
  return parts.join(' ')
}
