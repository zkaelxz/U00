import { describe, expect, it } from 'vitest'

import {
  attachNotice, bilingualCredit, coverFileProblem, creditRows, epubRange, hasCredits,
} from './preambleForm'

const drama = {
  author: '墨香铜臭', studio: '晋江文学城', director: null, voice_actors: '',
  author_romanized: 'Mo Xiang Tong Xiu', studio_romanized: null, director_romanized: null, voice_actors_romanized: null,
}

describe('credits', () => {
  it('formats a credit bilingually', () => {
    expect(bilingualCredit('墨香铜臭', 'Mo Xiang Tong Xiu')).toBe('Mo Xiang Tong Xiu (墨香铜臭)')
    expect(bilingualCredit('Same', 'Same')).toBe('Same')
    expect(bilingualCredit(null, 'Only')).toBe('Only')
    expect(bilingualCredit(' 原文 ', null)).toBe('原文')
  })

  it('lists only the set credits', () => {
    expect(creditRows(drama)).toEqual([
      { key: 'author', label: 'Author', text: 'Mo Xiang Tong Xiu (墨香铜臭)', romanized: true },
      { key: 'studio', label: 'Studio', text: '晋江文学城', romanized: false },
    ])
    expect(hasCredits(drama)).toBe(true)
    expect(hasCredits({ author: null, studio: ' ', director: null, voice_actors: null })).toBe(false)
  })
})

describe('cover file', () => {
  const f = (name: string, type: string, size = 100) => ({ name, type, size })
  it('accepts PNG, JPEG and WebP up to 10 MB', () => {
    expect(coverFileProblem(null)).toBeNull()
    expect(coverFileProblem(f('a.png', 'image/png'))).toBeNull()
    expect(coverFileProblem(f('a.JPG', ''))).toBeNull()
    expect(coverFileProblem(f('a.gif', 'image/gif'))).toBe('Choose a PNG, JPEG or WebP image.')
    expect(coverFileProblem(f('a.svg', 'image/svg+xml'))).toBe('Choose a PNG, JPEG or WebP image.')
    expect(coverFileProblem(f('a.png', 'image/png', 11 * 1024 * 1024))).toBe('That image is larger than 10 MB.')
    expect(coverFileProblem(f('a.png', 'image/png', 0))).toBe('That file is empty.')
  })
})

describe('EPUB range', () => {
  it('parses blank ends and rejects bad numbers', () => {
    expect(epubRange('', '')).toEqual({ from: undefined, to: undefined })
    expect(epubRange('2', ' 5 ')).toEqual({ from: 2, to: 5 })
    expect(epubRange('0', '')).toEqual({ problem: 'Chapter numbers are whole numbers from 1.' })
    expect(epubRange('1.5', '')).toEqual({ problem: 'Chapter numbers are whole numbers from 1.' })
    expect(epubRange('6', '5')).toEqual({ problem: 'The first chapter comes after the last.' })
  })

  it('says what was attached', () => {
    expect(attachNotice({ char_count: 1200 })).toBe('Attached 1,200 characters.')
    expect(attachNotice({ char_count: 5, epub_chapters: 40, chapter_from: 2, chapter_to: 5 }))
      .toBe('Attached 5 characters (chapters 2–5 of 40).')
    expect(attachNotice({ char_count: 5, epub_chapters: 40, chapter_from: 7, chapter_to: 7 }))
      .toBe('Attached 5 characters (chapter 7 of 40).')
    expect(attachNotice({ char_count: 5, epub_chapters: 3, chapter_from: 1, chapter_to: 3 }))
      .toBe('Attached 5 characters (all 3 chapters).')
  })
})
