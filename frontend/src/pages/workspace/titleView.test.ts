import { describe, expect, it } from 'vitest'

import { asTitleView, defaultTitleView, episodeRows, episodeState, hasSimpleView, opensSimpleStage, titleViewPref } from './titleView'

describe('title view', () => {
  it('gives the stages a Simple page links to a slim page, and nothing else', () => {
    for (const k of ['audio', 'video', 'novel', 'comic'] as const) {
      for (const st of ['source', 'translate', 'export']) expect(opensSimpleStage(k, st)).toBe(true)
      expect(opensSimpleStage(k, null)).toBe(false)
    }
    for (const k of ['audio', 'video'] as const) expect(opensSimpleStage(k, 'review')).toBe(true)
    for (const k of ['novel', 'comic'] as const) expect(opensSimpleStage(k, 'review')).toBe(false)
    expect(opensSimpleStage('subtitle', 'translate')).toBe(false)
  })

  it('defaults to Full so nothing changes until the switch is used', () => {
    expect(asTitleView(undefined)).toBe('full')
    expect(asTitleView('nonsense')).toBe('full')
    expect(asTitleView('simple')).toBe('simple')
  })

  it('offers Simple for every kind but a subtitle-only title', () => {
    for (const k of ['audio', 'video', 'novel', 'comic'] as const) expect(hasSimpleView(k)).toBe(true)
    expect(hasSimpleView('subtitle')).toBe(false)
  })

  it('lands a novel on its page and keeps one saved choice per group of kinds', () => {
    expect(defaultTitleView('novel')).toBe('simple')
    for (const k of ['audio', 'video', 'comic'] as const) expect(defaultTitleView(k)).toBe('full')
    expect(asTitleView('', 'simple')).toBe('simple')
    expect(asTitleView('full', 'simple')).toBe('full')
    expect(new Set([titleViewPref('audio'), titleViewPref('video'), titleViewPref('novel'), titleViewPref('comic')]).size).toBe(3)
  })

  it('words an episode by how far its lines are', () => {
    expect(episodeState(0, 0)).toBe('Not transcribed')
    expect(episodeState(10, 0)).toBe('Transcribed · Not translated')
    expect(episodeState(10, 4)).toBe('Transcribed · 4 of 10 translated')
    expect(episodeState(10, 10)).toBe('Transcribed · Translated')
  })

  it('orders episodes by number, unnumbered last, and names an untitled one', () => {
    const rows = episodeRows([
      { id: 5, title_en: null, title_zh: null, episode_number: null, line_count: 0, translated_count: 0 },
      { id: 9, title_en: 'Two', title_zh: null, episode_number: 2, line_count: 3, translated_count: 0 },
      { id: 7, title_en: null, title_zh: '一', episode_number: 1, line_count: 3, translated_count: 3 },
    ])
    expect(rows.map((r) => [r.id, r.title])).toEqual([[7, '一'], [9, 'Two'], [5, 'Title #5']])
  })
})
