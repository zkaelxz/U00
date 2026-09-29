import { describe, expect, it } from 'vitest'

import {
  MAX_NOVEL_TEXT_CHARS,
  checkNovelFile,
  novelFileStatusLine,
  novelFileSummary,
  pasteProblem,
  pastedCount,
} from './novelFile'
import { bumpNovelFiles } from './novelFileEvents'

describe('paste box', () => {
  it('counts the trimmed text, as the server stores it', () => {
    expect(pastedCount('')).toBe('0 characters')
    expect(pastedCount('  a \n')).toBe('1 character')
    expect(pastedCount('第一章 abc')).toBe('7 characters')
  })

  it('flags text over the server limit only', () => {
    expect(pasteProblem('')).toBeNull()
    expect(pasteProblem('x'.repeat(10))).toBeNull()
    expect(pasteProblem(' '.repeat(5) + 'x'.repeat(MAX_NOVEL_TEXT_CHARS))).toBeNull()
    expect(pasteProblem('x'.repeat(MAX_NOVEL_TEXT_CHARS + 1))).toMatch(/Too long/)
  })

  it('bumpNovelFiles is callable without subscribers', () => {
    expect(() => bumpNovelFiles()).not.toThrow()
  })
})

describe('checkNovelFile', () => {
  it('accepts the server whitelist per kind, case-insensitively', () => {
    expect(checkNovelFile('reference', 'Book.TXT')).toBeNull()
    expect(checkNovelFile('reference', 'notes.md')).toBeNull()
    expect(checkNovelFile('raw', 'novel.epub')).toBeNull()
    expect(checkNovelFile('raw', 'C:\\books\\n.txt')).toBeNull()
  })

  it('rejects other types, including an EPUB as the reference', () => {
    expect(checkNovelFile('reference', 'novel.epub')).toMatch(/novel\.epub is not a \.txt, \.md file/)
    expect(checkNovelFile('raw', 'scan.pdf')).toMatch(/\.txt, \.md, \.epub/)
    expect(checkNovelFile('raw', 'noext')).not.toBeNull()
    expect(checkNovelFile('raw', '.txt')).not.toBeNull()
  })
})

describe('status wording', () => {
  it('summarises and describes a saved file', () => {
    const s = { drama_id: 1, present: true, size_bytes: 20480, char_count: 12345 }
    expect(novelFileSummary(s)).toBe(`${(12345).toLocaleString()} chars`)
    expect(novelFileStatusLine(s)).toBe(`Saved: ${(12345).toLocaleString()} characters (20 KB).`)
    expect(novelFileStatusLine({ ...s, size_bytes: 12 })).toContain('(12 bytes)')
  })

  it('says when nothing is saved or status is loading', () => {
    const none = { drama_id: 1, present: false, size_bytes: 0, char_count: 0 }
    expect(novelFileSummary(none)).toBe('none saved')
    expect(novelFileStatusLine(none)).toBe('Nothing saved yet.')
    expect(novelFileSummary(null)).toBe('checking')
    expect(novelFileStatusLine(null)).toBe('Checking…')
  })
})
