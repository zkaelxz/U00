// Why a stage's primary button can't run yet (guideline rules 2 and 22).
// The stage shows the reason under the disabled button, with a one-tap fix.

export type TranslateBlocker =
  | { kind: 'no-lines' }
  | { kind: 'all-translated'; total: number }
  | { kind: 'confirm-force' }
  | null

export function translateBlocker(
  lineCount: number,
  untranslated: number,
  force: boolean,
  forceConfirmed: boolean,
): TranslateBlocker {
  if (lineCount <= 0) return { kind: 'no-lines' }
  if (force && !forceConfirmed) return { kind: 'confirm-force' }
  if (!force && untranslated <= 0) return { kind: 'all-translated', total: lineCount }
  return null
}

// null total = readiness not loaded (or failed): don't block, the server decides.
export function exportBlocked(totalLines: number | null): boolean {
  return totalLines !== null && totalLines <= 0
}

// The Source stage's media file input, so the Transcribe blocker can focus it.
export const mediaFileInputId = (dramaId: number) => `source-media-file-${dramaId}`
