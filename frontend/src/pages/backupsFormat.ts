// Pure helpers for the automatic backup UI: the Settings "Automatic backups"
// Card (settings/AutoBackupCard.tsx) and the Library tools snapshot block
// (libraryAdmin/SnapshotBlock.tsx).
import { ApiError } from '../api/client'
import { describeError, safeDetail } from '../components/errorMessages'
import type {
  AutoBackupSettings,
  AutoBackupSettingsUpdate,
  BackupFrequency,
  CopyCandidate,
  RestoreDramaDone,
  SnapshotCopy,
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

export const ROTATION_NOTE =
  'Keeps one copy a day for the last 2 days, plus the first copy of each of the last 2 weeks. Older copies this library made are deleted after each backup.'
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

export const CHOOSE_COPY_TEXT =
  "The newest copy can't be told for sure (copies from another library or from before this update, or dates that disagree), so none is picked for you. Choose the copy to restore from."

/** One line for the current snapshot, for both places that show it. */
export function describeSnapshot(s: SnapshotInfo | null, opts: DateOpts = {}): string {
  if (!s) return 'Checking for a snapshot…'
  if (!s.exists) return 'No snapshot yet.'
  if (s.readable === false) return "A snapshot file is there, but it can't be read. Back up again to replace it."
  if (s.choose_copy) return "Can't be told for sure — choose a copy when restoring."
  const when = formatWhen(s.created_at, opts)
  return `${when ? `${when} · ` : ''}${snapshotFacts(s)}`
}

/**
 * One copy in the copies list: "30 Sept 2026, 08:00 · Database only · 12.3 MB · daily".
 * A copy that can't be read says so instead of its kind and size; one this
 * library doesn't manage says "not managed".
 */
export function describeCopy(c: SnapshotCopy, opts: DateOpts = {}): string {
  const when = formatWhen(c.created_at, opts) ?? c.name
  const tail = isManaged(c) ? [] : ['not managed']
  if (!c.readable) return [when, "can't be read", ...tail].join(' · ')
  const parts = [when, snapshotKindLabel(c.kind), formatBytes(c.size)]
  if (c.kept_as) parts.push(c.kept_as)
  return [...parts, ...tail].join(' · ')
}

// Copies this library doesn't manage: listed, never rotated or removed by
// "delete all", restorable or deletable only when chosen by name.
export const UNMANAGED_LABEL = 'Other or older copies (not managed)'
export const UNMANAGED_NOTE =
  "Made by another library sharing this folder, before this update, or unreadable. Automatic rotation and \"delete all\" never remove them."
export const UNMANAGED_RESTORE_WARNING =
  "This copy wasn't made by this library (it may be another PC's library, or from before this update). Check its date and dramas before restoring."
export const UNMANAGED_DELETE_WARNING =
  "Not managed by this library: it may be another PC's backup, and that PC won't know it is gone."

/** A copy with no `managed` field (an older server) counts as managed. */
export const isManaged = (c: Pick<SnapshotCopy, 'managed'>) => c.managed !== false

/** The copies (order kept) split into this library's and the unmanaged ones. */
export function splitCopies(copies: readonly SnapshotCopy[]): { managed: SnapshotCopy[]; unmanaged: SnapshotCopy[] } {
  return { managed: copies.filter(isManaged), unmanaged: copies.filter((c) => !isManaged(c)) }
}

/**
 * The copy the restore picker starts on: the server's default copy, or ""
 * when there is none (choose_copy), so the owner must pick one. Never a guess
 * from the list's order.
 */
export function initialRestoreCopy(s: SnapshotInfo | null, readable: readonly SnapshotCopy[]): string {
  if (!s || s.choose_copy || !s.default_copy) return ''
  return readable.some((c) => c.name === s.default_copy) ? s.default_copy : ''
}

/** The candidates of a 409 "choose_copy" answer, or null for any other error. */
export function chooseCopyCandidates(e: unknown): CopyCandidate[] | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null
  const d = e.details as { reason?: unknown; candidates?: unknown } | null | undefined
  if (!d || typeof d !== 'object' || d.reason !== 'choose_copy' || !Array.isArray(d.candidates)) return null
  return d.candidates.filter(
    (c): c is CopyCandidate => !!c && typeof c === 'object' && typeof (c as CopyCandidate).name === 'string',
  )
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
  'Use the full path of an existing folder outside the library (for example D:\\Backups), or leave it empty.'

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
