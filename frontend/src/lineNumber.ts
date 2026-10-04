// Line.idx is 0-based everywhere in the backend, but people see 1-based line
// numbers; use this at every display site and never send a displayed number
// back to the API as an idx.
export function lineNumber(idx: number): number {
  return idx + 1
}

/** A displayed (1-based) line number back to the idx it names. */
export function idxFromLineNumber(n: number): number {
  return n - 1
}
