import { describe, expect, it } from 'vitest'

import { asTitleView, episodeRows, episodeState, hasSimpleView } from './titleView'

describe('title view', () => {
  it('defaults to Full so nothing changes until the switch is used', () => {
    expect(asTitleView(undefined)).toBe('full')
    expect(asTitleView('nonsense')).toBe('full')
    expect(asTitleView('simple')).toBe('simple')
  })

  it('offers Simple for audio and video titles only', () => {
    expect(hasSimpleView('audio')).toBe(true)
    expect(hasSimpleView('video')).toBe(true)
    for (const k of ['novel', 'comic', 'subtitle'] as const) expect(hasSimpleView(k)).toBe(false)
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
