import type { CompareSelection } from '../../../../types/workspace'

export type SelectionMode = 'line' | 'range' | 'flagged' | 'speaker' | 'time'

export interface SelectionForm {
  mode: SelectionMode
  lineNumber: string
  from: string
  to: string
  speaker: string
  startSeconds: string
  endSeconds: string
}

export const EMPTY_SELECTION_FORM: SelectionForm = {
  mode: 'flagged', lineNumber: '', from: '', to: '', speaker: '', startSeconds: '', endSeconds: '',
}

const wholeNumber = (v: string) => (/^\d+$/.test(v.trim()) && Number(v) >= 1 ? Number(v) : null)
const seconds = (v: string) => (v.trim() !== '' && Number.isFinite(Number(v)) && Number(v) >= 0 ? Number(v) : null)

// The request's selection, or the plain-words reason the form can't be sent yet.
export function buildSelection(f: SelectionForm): { selection: CompareSelection } | { problem: string } {
  switch (f.mode) {
    case 'flagged':
      return { selection: { kind: 'flagged' } }
    case 'line': {
      const n = wholeNumber(f.lineNumber)
      return n === null ? { problem: 'Enter a line number.' } : { selection: { kind: 'range', from_number: n, to_number: n } }
    }
    case 'range': {
      const a = wholeNumber(f.from)
      const b = wholeNumber(f.to)
      if (a === null || b === null) return { problem: 'Enter the first and last line numbers.' }
      if (a > b) return { problem: 'The first line number must not be after the last.' }
      return { selection: { kind: 'range', from_number: a, to_number: b } }
    }
    case 'speaker':
      return f.speaker.trim() ? { selection: { kind: 'speaker', speaker: f.speaker.trim() } } : { problem: 'Choose a speaker.' }
    case 'time': {
      const a = seconds(f.startSeconds)
      const b = seconds(f.endSeconds)
      if (a === null || b === null) return { problem: 'Enter the start and end in seconds.' }
      if (a >= b) return { problem: 'The start must be before the end.' }
      return { selection: { kind: 'time', start_seconds: a, end_seconds: b } }
    }
  }
}

export function capProblem(lineCount: number, maxLines: number): string | null {
  return lineCount > maxLines
    ? `That is ${lineCount} lines; one run is limited to ${maxLines}. Narrow the selection and run it in parts.`
    : null
}

export interface DiffPart {
  text: string
  changed: boolean
}

// CJK characters one by one (there are no spaces to split on), Latin words and
// numbers whole, everything else (spaces, punctuation) as single tokens.
const TOKEN = /[぀-ヿ㐀-鿿가-힯]|[A-Za-z0-9']+|\s+|[^]/gu

const tokens = (s: string) => s.match(TOKEN) ?? []

function pushPart(parts: DiffPart[], text: string, changed: boolean) {
  const last = parts[parts.length - 1]
  if (last && last.changed === changed) last.text += text
  else parts.push({ text, changed })
}

// Longest-common-subsequence diff of two texts: `a` marks what is not in `b`
// and `b` marks what is not in `a`. Whitespace never counts as a change.
export function textDiff(a: string, b: string): { a: DiffPart[]; b: DiffPart[] } {
  const x = tokens(a)
  const y = tokens(b)
  const dp: number[][] = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0))
  for (let i = x.length - 1; i >= 0; i--) {
    for (let j = y.length - 1; j >= 0; j--) {
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const outA: DiffPart[] = []
  const outB: DiffPart[] = []
  let i = 0
  let j = 0
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) {
      pushPart(outA, x[i], false)
      pushPart(outB, y[j], false)
      i++
      j++
    } else if (j >= y.length || (i < x.length && dp[i + 1][j] >= dp[i][j + 1])) {
      pushPart(outA, x[i], !/^\s+$/.test(x[i]))
      i++
    } else {
      pushPart(outB, y[j], !/^\s+$/.test(y[j]))
      j++
    }
  }
  return { a: bridgeGaps(outA), b: bridgeGaps(outB) }
}

// Unchanged whitespace between two changed stretches reads as one change.
function bridgeGaps(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = []
  for (const p of parts) {
    const last = out[out.length - 1]
    const before = out[out.length - 2]
    if (last && before && p.changed && before.changed && !last.changed && /^\s+$/.test(last.text)) {
      out.splice(out.length - 2, 2, { text: before.text + last.text + p.text, changed: true })
    } else {
      out.push({ ...p })
    }
  }
  return out
}

export function isSameText(a: string, b: string): boolean {
  return a.trim() === b.trim()
}

// A finished compare job: ready to fetch, or why there is nothing to show.
export function compareOutcome(job: {
  status: string
  outcome?: string | null
  result?: Record<string, unknown> | null
}): { kind: 'ready' } | { kind: 'none'; text: string } {
  const reason = job.result?.failed_reason
  if (job.status === 'done' && !reason && job.outcome !== 'failed') return { kind: 'ready' }
  if (job.status === 'cancelled' || reason === 'cancelled' || job.outcome === 'cancelled') {
    return { kind: 'none', text: 'Cancelled before any line was heard. Nothing was changed.' }
  }
  if (reason === 'model_download') return { kind: 'none', text: 'The speech model could not be downloaded.' }
  return { kind: 'none', text: 'The comparison failed. Nothing was changed.' }
}
