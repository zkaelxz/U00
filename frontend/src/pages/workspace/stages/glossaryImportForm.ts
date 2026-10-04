// Pure helpers for the Glossary panel's import (parity T03), so they can be unit tested.

import type { GlossaryImportResult } from '../../../types/translateStage'

// Same cap as api/schemas/characters.py GlossaryImportRequest.text.
export const MAX_IMPORT_CHARS = 1_000_000
export const GLOSSARY_FILE_ACCEPT = '.csv,.tsv,.json,.txt,text/csv,text/tab-separated-values,application/json,text/plain'

export function validateImportText(text: string): string | null {
  if (!text.trim()) return 'Paste a glossary or load a file first.'
  if (text.length > MAX_IMPORT_CHARS) return 'That glossary is too long to import at once (1,000,000 characters at most).'
  return null
}

const count = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

// One line: what was added, replaced, skipped and refused.
export function importSummary(r: GlossaryImportResult): string {
  const parts = [`Added ${count(r.added.length, 'term')}`]
  if (r.overwritten.length) parts.push(`replaced ${r.overwritten.length}`)
  if (r.skipped_existing.length) parts.push(`kept ${r.skipped_existing.length} existing`)
  if (r.invalid.length) parts.push(`skipped ${r.invalid.length} too long`)
  return `${parts.join(', ')}.`
}
