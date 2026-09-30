// Pure helpers for "From a backup file…" in Library tools (libraryAdmin/BackupFileImport.tsx).
import type { BackupFileDrama, BackupFileDramaList, ImportDramasDone } from '../types/backups'
import { isoDay } from './backupsFormat'

const plural = (n: number, one: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : `${one}s`}`

/** The chosen ids after ticking or unticking one, in list order. */
export function toggleId(list: readonly BackupFileDrama[], chosen: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(chosen)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return new Set(list.filter((d) => next.has(d.id)).map((d) => d.id))
}

/** The notes shown before the RESTORE confirm. */
export function importNotes(list: BackupFileDramaList, chosen: readonly BackupFileDrama[], today: string = isoDay()): string[] {
  const notes = [
    'Each drama is added as a new drama; nothing you have is replaced or merged.',
    `If a title is already in your library, '(restored ${today})' is added to the new one.`,
    'They belong to you and follow your sharing setting (private unless you share new items).',
    `A series comes back as a new series named '… (imported ${today})', with its glossary and characters.`,
    'Reading progress and personal notes from the file are not imported.',
  ]
  if (list.kind === 'database' || !list.media_available) {
    notes.push('Files (audio/video/pages) are not in this file; only the text and settings come in.')
  } else if (chosen.some((d) => d.has_media)) {
    notes.push('Files come in for the dramas marked "with files".')
  } else {
    notes.push('None of the chosen dramas has files in this file.')
  }
  if (list.schema_differs) {
    notes.push('The file is from a different version; anything this version does not know is left out.')
  }
  return notes
}

/** The result line after an import. */
export function describeImport(r: ImportDramasDone): string {
  const parts = [`Imported ${plural(r.imported.length, 'drama')}.`]
  const lines = r.counts?.lines
  if (typeof lines === 'number') parts.push(`${plural(lines, 'line')}.`)
  if (r.series_created) parts.push(`${r.series_created} new series.`)
  parts.push(r.media_imported ? `Files for ${plural(r.media_imported, 'drama')}.` : 'No files were imported.')
  return parts.join(' ')
}
