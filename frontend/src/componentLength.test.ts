/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

// A component function over MAX_LINES is hard to review, and nothing else stops it growing a line
// at a time. New ones must stay under the ceiling; the ones already over it may shrink but never
// grow. A column-0 closing brace ends a top-level function because the code is formatted that way,
// so no parser is needed. Retire this once an unused-complexity lint covers the frontend.
const MAX_LINES = 400

const KNOWN_OVER: Record<string, number> = {
  'pages/workspace/stages/review/LinesPanel.tsx:LinesPanel': 474,
  'pages/Comic.tsx:ComicPage': 555,
  'pages/workspace/stages/review/Player.tsx:Player': 540,
  'pages/Live.tsx:LivePage': 486,
  'pages/workspace/stages/GlossaryPanel.tsx:GlossaryBody': 416,
}

const COMPONENT_START = /^(?:export (?:default )?)?(?:function ([A-Z]\w*)\(|const ([A-Z]\w*)(?::[^=]+)? = (?:memo\(|forwardRef\(|\())/

const root = new URL('./', import.meta.url).pathname

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const path = join(dir, e.name)
    if (e.isDirectory()) return sourceFiles(path)
    return path.endsWith('.tsx') && !path.includes('.test.') ? [path] : []
  })
}

function componentLengths(): Record<string, number> {
  const lengths: Record<string, number> = {}
  for (const file of sourceFiles(root)) {
    const lines = readFileSync(file, 'utf8').split('\n')
    for (let i = 0; i < lines.length; i++) {
      const m = COMPONENT_START.exec(lines[i])
      if (!m) continue
      let end = i + 1
      while (end < lines.length && !/^[})]/.test(lines[end])) end++
      lengths[`${relative(root, file)}:${m[1] ?? m[2]}`] = end - i + 1
      i = end
    }
  }
  return lengths
}

describe('component length', () => {
  const lengths = componentLengths()

  it(`keeps every component under ${MAX_LINES} lines unless it is already listed`, () => {
    const over = Object.entries(lengths).filter(([key, n]) => n > MAX_LINES && !(key in KNOWN_OVER))
    expect(over).toEqual([])
  })

  it('never lets a listed component grow, and drops it from the list once it is under the ceiling', () => {
    for (const [key, limit] of Object.entries(KNOWN_OVER)) {
      expect(lengths[key], `${key} is missing: rename it in the list`).toBeDefined()
      expect(lengths[key], `${key} grew past ${limit}`).toBeLessThanOrEqual(limit)
      expect(lengths[key], `${key} is now under ${MAX_LINES}: remove it from KNOWN_OVER`).toBeGreaterThan(MAX_LINES)
    }
  })
})
