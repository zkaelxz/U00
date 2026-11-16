/** Which title-page button leads: lines with English everywhere mean Review, lines without mean Translate. */
export function mainButton(lineCount: number | null, untranslated: number): 'translate' | 'review' | null {
  if (!lineCount) return null
  return untranslated === 0 ? 'review' : 'translate'
}
