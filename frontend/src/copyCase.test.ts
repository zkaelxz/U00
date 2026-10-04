// Sentence-case guard: text people read starts with a capital letter. This
// scans the central label tables and the literal label/title/summary/help
// props in the pages, so a new lowercase label fails here instead of shipping.
// Product and tool names that are written in lower case (ffmpeg, yt-dlp, ...)
// and identifiers pass; see capFirst in labels.ts.
import { describe, expect, it } from 'vitest'

import { capFirst } from './labels'

const raw = import.meta.glob<string>(['/src/**/*.{ts,tsx}', '!/src/**/*.test.ts', '!/src/__*'], {
  query: '?raw',
  import: 'default',
  eager: true,
})
const loaders = import.meta.glob<Record<string, unknown>>(['/src/**/*.ts', '!/src/**/*.test.ts', '!/src/__*'])

const LABEL_EXPORT = /^export const ((?!DEFAULT_)[A-Z][A-Z_]*(?:LABELS?|WORDS|OPTIONS|TEXT|NOTE|HELP|MESSAGE))\b/gm
const NAME = /(?:LABELS?|WORDS|OPTIONS|TEXT|NOTE|HELP|MESSAGE)$/

// The strings a person would read in a label table: record values, option
// labels (the last item of a [value, label] pair, or .label), plain strings.
function texts(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value)
  else if (Array.isArray(value)) {
    const last = value[value.length - 1]
    if (value.length === 2 && typeof value[0] !== 'object' && typeof last === 'string') out.push(last)
    else for (const v of value) texts(v, out)
  } else if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>
    if (typeof o.label === 'string') out.push(o.label)
    else for (const [k, v] of Object.entries(o)) if (!/^(testId|storageKey|id|key|value|kind|code)$/.test(k)) texts(v, out)
  }
  return out
}

const startsLower = (s: string) => /^[a-z]/.test(s) && capFirst(s) !== s

describe('sentence case', () => {
  it('starts every central label with a capital letter', async () => {
    const bad: string[] = []
    let tables = 0
    for (const [path, src] of Object.entries(raw)) {
      if (!path.endsWith('.ts') || !loaders[path]) continue
      const names = [...src.matchAll(LABEL_EXPORT)].map((m) => m[1])
      if (!names.length) continue
      const mod = await loaders[path]()
      for (const name of names) {
        if (!NAME.test(name) || !(name in mod)) continue
        tables++
        for (const t of texts(mod[name])) if (startsLower(t)) bad.push(`${path} ${name}: "${t}"`)
      }
    }
    expect(tables).toBeGreaterThan(30)
    expect(bad).toEqual([])
  })

  it('starts the literal label, title, summary and help props with a capital letter', () => {
    const bad: string[] = []
    for (const [path, src] of Object.entries(raw)) {
      if (!path.endsWith('.tsx')) continue
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '')
      for (const m of code.matchAll(/\b(label|title|aria-label|ariaLabel|summary|help)="([^"{]*)"/g)) {
        if (startsLower(m[2])) bad.push(`${path} ${m[1]}="${m[2]}"`)
      }
    }
    expect(bad).toEqual([])
  })

  it('finds a lowercase label (the guard itself works)', () => {
    expect(startsLower('none saved')).toBe(true)
    expect(startsLower('None saved')).toBe(false)
    expect(startsLower('yt-dlp reads cookies')).toBe(false)
    expect(startsLower('')).toBe(false)
  })
})
