import { describe, expect, it } from 'vitest'

import type { BackupFileDrama, BackupFileDramaList } from '../types/backups'
import { describeImport, importNotes, toggleId } from './backupFileImportModel'

const A: BackupFileDrama = { id: 1, title: 'A', media_type: 'novel', line_count: 3, has_media: true }
const B: BackupFileDrama = { id: 2, title: 'B', media_type: 'novel', line_count: 1, has_media: false }
const list = (over: Partial<BackupFileDramaList> = {}): BackupFileDramaList =>
  ({ kind: 'zip', media_available: true, schema_differs: false, dramas: [A, B], ...over })

describe('backup file import model', () => {
  it('toggles ids and keeps list order', () => {
    const one = toggleId([A, B], new Set(), 2)
    const two = toggleId([A, B], one, 1)
    expect([...two]).toEqual([1, 2])
    expect([...toggleId([A, B], two, 2)]).toEqual([1])
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
    expect(describeImport({ imported: [{ source_id: 1, drama_id: 5, title: 'A', media_imported: false }], series_created: 0, media_imported: 0, counts: { lines: 1 } }))
      .toBe('Imported 1 title. 1 line. No files were imported.')
  })
})
