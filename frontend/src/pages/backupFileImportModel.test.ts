import { describe, expect, it } from 'vitest'

import type { BackupFileDrama, BackupFileDramaList } from '../types/backups'
import { describeImport, importNotes, toggleBackupDrama } from './backupFileImportModel'

const A: BackupFileDrama = { id: 1, title: 'A', media_type: 'novel', line_count: 3, has_media: true }
const B: BackupFileDrama = { id: 2, title: 'B', media_type: 'novel', line_count: 1, has_media: false }
const list = (over: Partial<BackupFileDramaList> = {}): BackupFileDramaList =>
  ({ kind: 'zip', media_available: true, schema_differs: false, voice_bank_entries: 0, settings_available: false, dramas: [A, B], ...over })

describe('backup file import model', () => {
  it('toggles ids and keeps list order', () => {
    const one = toggleBackupDrama([A, B], new Set(), 2)
    const two = toggleBackupDrama([A, B], one, 1)
    expect([...two]).toEqual([1, 2])
    expect([...toggleBackupDrama([A, B], two, 2)]).toEqual([1])
  })

  it('notes say what is and is not imported', () => {
    const notes = importNotes(list(), [A], '2026-09-30')
    expect(notes).toContain("If a title is already in your library, '(restored 2026-09-30)' is added to the new one.")
    expect(notes.join(' ')).toContain('Files come in for the titles marked "with files".')
    expect(notes.join(' ')).toContain('personal notes from the file are not imported')
  })

  it('files note follows the file and the choice', () => {
    expect(importNotes(list({ kind: 'database', media_available: false }), [A]).join(' ')).toContain('Files (audio/video/pages) are not in this file')
    expect(importNotes(list(), [B]).join(' ')).toContain('None of the chosen titles has files')
    expect(importNotes(list({ schema_differs: true }), [A]).join(' ')).toContain('different version')
  })

  it('describes the result', () => {
    expect(describeImport({ imported: [{ source_id: 1, drama_id: 5, title: 'A', media_imported: false }], series_created: 0, series_joined: 0, media_imported: 0, voice_bank_imported: 0, settings_imported: 0, notes: [], counts: { lines: 1 } }))
      .toBe('Imported 1 title. 1 line. No files were imported.')
    expect(describeImport({ imported: [{ source_id: 1, drama_id: 5, title: 'A', media_imported: false }], series_created: 1, series_joined: 2, media_imported: 0, voice_bank_imported: 0, settings_imported: 0, notes: [], counts: {} }))
      .toBe('Imported 1 title. 1 new series. Added to 2 existing series. No files were imported.')
  })

  it('says what came in from the voice bank and settings, and that keys did not', () => {
    expect(describeImport({ imported: [], series_created: 0, series_joined: 0, media_imported: 0, voice_bank_imported: 2, settings_imported: 1, notes: ['API keys are never in a backup.'], counts: {} }))
      .toBe('Imported 0 titles. 2 voices added to the voice bank. 1 setting taken from the file. No files were imported. API keys are never in a backup.')
    const notes = importNotes(list(), [A], '2026-09-30', { voiceBank: true, settings: true }).join(' ')
    expect(notes).toContain("voice bank entries are added")
    expect(notes).toContain('API keys, the monthly cap, the Gemini free-tier switch, the job pop-up, the sharing default, folders, app updates and hardware settings stay as they are here')
  })

  it('the series note follows the join choice', () => {
    expect(importNotes(list(), [A], '2026-09-30').join(' ')).toContain("new series named '… (imported 2026-09-30)'")
    const joined = importNotes(list(), [A], '2026-09-30', { joinSeries: true }).join(' ')
    expect(joined).toContain('is added to that series')
    expect(joined).toContain('voice prints combined')
  })
})
