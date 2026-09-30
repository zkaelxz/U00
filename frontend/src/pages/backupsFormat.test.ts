import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { AutoBackupSettings, RestoreDramaDone, SnapshotDrama, SnapshotInfo } from '../types/backups'
import {
  FOLDER_RULES, backupNowStep, changedSettings, describeRestore, describeSnapshot, filterSnapshotDramas, folderChange, formatWhen,
  isoDay, needsReplaceConfirm, nextRunText, replaceWarning, serverSentence, settingsSummary, snapshotFacts,
  snapshotKindLabel, restoreNotes,
} from './backupsFormat'

const UTC = { locale: 'en-GB', timeZone: 'UTC' }

const SETTINGS: AutoBackupSettings = {
  enabled: false, frequency: 'weekly', include_media: false, folder: '', frequencies: ['daily', 'weekly', 'monthly'],
  last_run_at: null, last_attempt_at: null, last_error: null, next_run_at: null, running: false,
}

const SNAP: SnapshotInfo = {
  exists: true, readable: true, created_at: '2026-09-28T09:30:00+00:00', kind: 'db-only', size: 12_345_678,
  app_version: '1.0', drama_count: 3,
}

const DRAMA: SnapshotDrama = { id: 4, title: 'Signal', media_type: 'audio_drama', line_count: 120, exists_now: false }

describe('dates and snapshot lines', () => {
  it('formats ISO UTC as a local date and time, null for missing or bad', () => {
    expect(formatWhen('2026-09-28T09:30:00+00:00', UTC)).toBe('28 Sept 2026, 09:30')
    expect(formatWhen('2026-09-28T09:30:00+00:00', { locale: 'en-GB', timeZone: 'Asia/Tokyo' })).toBe('28 Sept 2026, 18:30')
    expect(formatWhen(null)).toBeNull()
    expect(formatWhen('not a date')).toBeNull()
  })

  it('names the kind in plain words', () => {
    expect(snapshotKindLabel('db-only')).toBe('Database only')
    expect(snapshotKindLabel('full')).toBe('Database + media')
  })

  it('describes the snapshot: none, unreadable, and the full line', () => {
    expect(describeSnapshot(null)).toBe('Checking for a snapshot…')
    expect(describeSnapshot({ exists: false })).toBe('No snapshot yet.')
    expect(describeSnapshot({ exists: true, readable: false })).toMatch(/can't be read/)
    expect(describeSnapshot(SNAP, UTC)).toBe('28 Sept 2026, 09:30 · Database only · 12.3 MB · 3 dramas')
    expect(snapshotFacts({ exists: true, kind: 'full', size: 900, drama_count: 1 })).toBe('Database + media · 900 B · 1 drama')
  })

  it('summarises the settings for the Card header', () => {
    expect(settingsSummary(SETTINGS)).toBe('Off')
    expect(settingsSummary({ ...SETTINGS, enabled: true })).toBe('Weekly · database only')
    expect(settingsSummary({ ...SETTINGS, enabled: true, frequency: 'daily', include_media: true })).toBe('Daily · with media')
  })

  it('says when the next backup runs, or that it is due', () => {
    const now = new Date('2026-09-30T12:00:00Z')
    expect(nextRunText(SETTINGS, now)).toBeNull()
    expect(nextRunText({ ...SETTINGS, enabled: true, next_run_at: '2026-10-05T09:30:00+00:00' }, now, UTC))
      .toBe('Next backup: 5 Oct 2026, 09:30.')
    expect(nextRunText({ ...SETTINGS, enabled: true, next_run_at: '2026-09-30T11:00:00+00:00' }, now))
      .toMatch(/due now/)
  })
})

describe('Back up now: replace confirmation', () => {
  it('no snapshot: starts without replace', () => {
    expect(backupNowStep({ exists: false }, false)).toEqual({ kind: 'start', replace: false })
  })

  it('a snapshot: asks first, then starts with replace', () => {
    expect(backupNowStep(SNAP, false)).toEqual({ kind: 'confirm' })
    expect(backupNowStep(SNAP, true)).toEqual({ kind: 'start', replace: true })
  })

  it('snapshot unknown: starts without replace and lets the server refuse', () => {
    expect(backupNowStep(null, false)).toEqual({ kind: 'start', replace: false })
    const refused = new ApiError(422, {
      code: 'invalid_input',
      message: 'A backup snapshot already exists; backing up now replaces it. Send replace=true to confirm.',
    })
    expect(needsReplaceConfirm(refused)).toBe(true)
    expect(needsReplaceConfirm(new ApiError(409, { code: 'conflict', message: 'A backup is already running.' }))).toBe(false)
    expect(needsReplaceConfirm(new Error('x'))).toBe(false)
  })

  it('the warning names the date, kind and size of the snapshot being replaced', () => {
    expect(replaceWarning(SNAP, UTC)).toBe(
      'This replaces the snapshot from 28 Sept 2026, 09:30 (Database only · 12.3 MB · 3 dramas). Only one snapshot is kept.',
    )
    expect(replaceWarning(null)).toBe('This replaces the current snapshot.')
  })
})

describe('changed fields only', () => {
  it('folder: trimmed, null when unchanged, empty means the default', () => {
    expect(folderChange('  D:\\Backups ', '')).toBe('D:\\Backups')
    expect(folderChange(' D:\\Backups', 'D:\\Backups')).toBeNull()
    expect(folderChange('', 'D:\\Backups')).toBe('')
    expect(folderChange('   ', '')).toBeNull()
  })

  it('settings: keeps only values that differ from the saved ones', () => {
    expect(changedSettings(SETTINGS, { enabled: true, frequency: 'weekly' })).toEqual({ enabled: true })
    expect(changedSettings(SETTINGS, { frequency: 'monthly', include_media: false })).toEqual({ frequency: 'monthly' })
    expect(changedSettings(SETTINGS, { enabled: false })).toEqual({})
  })

  it('shows the server sentence for a refused folder, never a path', () => {
    expect(serverSentence(new ApiError(422, { code: 'invalid_input', message: "That backup folder doesn't exist. Create it first." })))
      .toBe("That backup folder doesn't exist. Create it first.")
    const pathy = new ApiError(422, { code: 'invalid_input', message: 'Bad folder /home/kae/secret' })
    expect(serverSentence(pathy)).toBe('Some of the values entered are not valid. Check them and try again.')
    expect(serverSentence(pathy, FOLDER_RULES)).toBe(FOLDER_RULES)
    expect(FOLDER_RULES).toContain('D:\\Backups')
    expect(serverSentence(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe('This only works on the main PC.')
  })
})

describe('restore one drama', () => {
  const many: SnapshotDrama[] = [
    DRAMA,
    { ...DRAMA, id: 5, title: 'Heaven Official’s Blessing' },
    { ...DRAMA, id: 12, title: 'Mo Dao Zu Shi' },
  ]

  it('filters by title (any case) or exact id', () => {
    expect(filterSnapshotDramas(many, '').map((d) => d.id)).toEqual([4, 5, 12])
    expect(filterSnapshotDramas(many, 'heaven').map((d) => d.id)).toEqual([5])
    expect(filterSnapshotDramas(many, ' 12 ').map((d) => d.id)).toEqual([12])
    expect(filterSnapshotDramas(many, 'zzz')).toEqual([])
  })

  it('isoDay pads month and day', () => {
    expect(isoDay(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('notes: a copy when still in the library; no files from a database-only snapshot', () => {
    const notes = restoreNotes({ ...DRAMA, exists_now: true }, 'db-only', '2026-09-30')
    expect(notes).toEqual([
      "Still in your library — it will be restored as a new copy titled 'Signal (restored 2026-09-30)'.",
      'Files (audio/video/pages) are not in this snapshot; only the text and settings come back.',
    ])
    expect(restoreNotes(DRAMA, 'full')).toEqual(['It comes back as it was when the snapshot was made.'])
  })

  it('describes the result in plain words', () => {
    const r: RestoreDramaDone = {
      drama_id: 9, restored_as_new: true, title: 'Signal (restored 2026-09-30)', media_restored: false,
      snapshot_kind: 'db-only', series: 'none', counts: { lines: 120, pages: 0 },
      skipped_tables: ['bulk_jobs', 'usage_log', 'other'],
    }
    expect(describeRestore(r)).toBe(
      "Restored 'Signal (restored 2026-09-30)' as a new drama. 120 lines. No files were in the snapshot. " +
        'Not restored: provider batch jobs and spending history.',
    )
    expect(describeRestore({ ...r, restored_as_new: false, title: 'Signal', media_restored: true, counts: { lines: 1 }, skipped_tables: [] }))
      .toBe("Restored 'Signal'. 1 line.")
    expect(describeRestore({ ...r, series: 'dropped_private', media_restored: true, skipped_tables: [] }))
      .toBe("Restored 'Signal (restored 2026-09-30)' as a new drama. 120 lines. " +
        "Its series is now someone else's private series, so the drama is back without a series.")
    expect(describeRestore({ ...r, series: 'recreated', media_restored: true, skipped_tables: [] }))
      .toContain('Its series was gone, so it was restored from the snapshot too.')
  })
})
