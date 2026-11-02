// Which speakers the line editor's picker lists.
// A value no line carries yet (typed earlier, or a speaker since renamed) stays selectable so the draft isn't silently changed.
export function speakerOptions(known: string[], value: string): string[] {
  return value && !known.includes(value) ? [...known, value] : known
}
