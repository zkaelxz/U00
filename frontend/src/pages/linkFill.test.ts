import { describe, expect, it } from 'vitest'

import type { MediaLinkProbe } from '../api/linkDetails'
import { createdFromLinkNote, defaultTicks, englishTitleCost, linkRows, linkValues, tickedValues } from './linkFill'

const probe: MediaLinkProbe = {
  kind: 'video', title: '配信 #12', channel: 'Some Channel', release_date: '2026-03-01', is_live: false,
  summary: '', platform: '', series_suggestion: 'Some Channel', note: '', display_url: 'https://www.youtube.com/watch',
}

describe('Fill from link preview', () => {
  it('offers a checkbox only for empty fields, ticked by default', () => {
    const rows = linkRows(linkValues(probe), { title_zh: '', author: '', release_date: '' })
    expect(rows.map((r) => [r.label, r.mode])).toEqual([['Title', 'fill'], ['Channel', 'fill'], ['Stream date', 'fill']])
    expect([...defaultTicks(rows)]).toEqual(['title_zh', 'author', 'release_date'])
  })

  it('never fills a field that has text: it only says what the link says', () => {
    const rows = linkRows(linkValues(probe), { title_zh: 'Typed', author: 'Some Channel', release_date: '' })
    expect(rows.find((r) => r.field === 'title_zh')).toMatchObject({ mode: 'differs', current: 'Typed', value: '配信 #12' })
    expect(rows.find((r) => r.field === 'author')?.mode).toBe('same')
    const all = new Set(rows.map((r) => r.field))
    expect(tickedValues(rows, all)).toEqual({ release_date: '2026-03-01' })
    expect(defaultTicks(rows)).toEqual(new Set(['release_date']))
  })

  it('writes only what is ticked', () => {
    const rows = linkRows(linkValues(probe), {})
    expect(tickedValues(rows, new Set())).toEqual({})
    expect(tickedValues(rows, new Set(['author']))).toEqual({ author: 'Some Channel' })
  })

  it('leaves out what the link does not say, and whitespace-only text counts as empty', () => {
    const rows = linkRows({ title_zh: ' ', author: 'A', release_date: '' }, { author: '   ' })
    expect(rows).toEqual([{ field: 'author', label: 'Channel', value: 'A', current: '', mode: 'fill' }])
  })

  it('lists the series only when asked for it (the scan)', () => {
    expect(linkRows(linkValues(probe), {}).some((r) => r.field === 'series')).toBe(false)
    expect(linkRows(linkValues(probe), {}, ['series'])[0]).toMatchObject({ label: 'Series', value: 'Some Channel' })
  })

  it('says how a nameless title was named', () => {
    expect(createdFromLinkNote('placeholder')).toMatch(/named after its website/)
    expect(createdFromLinkNote('link')).toMatch(/link's own title/)
    expect(createdFromLinkNote(null)).toBeNull()
  })

  it('shows the price of an English title suggestion before it runs', () => {
    expect(englishTitleCost('Claude', { estimated_usd: 0.001, cap_left_usd: 4.5, fits_cap: true }))
      .toBe('Uses Claude: under $0.01; cap left $4.50.')
    expect(englishTitleCost('GPT', { estimated_usd: 0.25, cap_left_usd: 0.1, fits_cap: false }))
      .toBe('Uses GPT: about $0.25; over the monthly cap, so it will not run.')
    expect(englishTitleCost('GPT', { estimated_usd: 0.25, cap_left_usd: null, fits_cap: true })).toBe('Uses GPT: about $0.25.')
  })
})
