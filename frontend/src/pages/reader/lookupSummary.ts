// The line after "Look up words": what was saved, and why nothing was when the dictionary can't help.
export function lookupSummary(saved: number, language: string, useAi: boolean): string {
  if (saved === 0 && !useAi && language !== 'zh') {
    return 'Saved 0 definitions. The built-in dictionary covers Chinese only: tick "Use AI" to define Japanese or Korean words.'
  }
  return saved === 1 ? 'Saved 1 definition.' : `Saved ${saved} definitions.`
}
