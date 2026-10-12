import { describe, expect, it } from 'vitest'

import { defaultSeriesSort, groupTitles, mediaMix, sortTitles, type GroupableTitle } from './titleOrder'

let n = 0
const t = (title: string, release_date: string | null, extra: Partial<GroupableTitle> = {}): GroupableTitle => ({
  id: ++n, title_en: title, title_zh: null, release_date, episode_number: null,
  series_id: null, media_type: 'anime', status: 'not started', ...extra,
})
const names = (xs: { title_en?: string | null }[]) => xs.map((x) => x.title_en)

describe('sortTitles', () => {
  const items = [t('c', '2025-03-01'), t('none1', null), t('a', '2024-01-01'), t('none2', ''), t('b', '2024-06-30')]

  it('oldest first puts titles without a date last, in the order given', () => {
    expect(names(sortTitles(items, 'date-asc'))).toEqual(['a', 'b', 'c', 'none1', 'none2'])
  })
  it('newest first still puts titles without a date last', () => {
    expect(names(sortTitles(items, 'date-desc'))).toEqual(['c', 'b', 'a', 'none1', 'none2'])
  })
  it('"added" keeps the order given and never mutates the input', () => {
    const copy = items.slice()
    expect(sortTitles(items, 'added')).toEqual(copy)
    sortTitles(items, 'date-asc')
    expect(items).toEqual(copy)
  })
  it('title sorts A-Z by the displayed name', () => {
    expect(names(sortTitles([t('b', null), t('a', null)], 'title'))).toEqual(['a', 'b'])
  })
  it('a tie on the day goes by episode number, then the order given', () => {
    const day = '2025-01-01'
    const tied = [t('x', day, { episode_number: null }), t('e2', day, { episode_number: 2 }), t('e1', day, { episode_number: 1 }), t('y', day)]
    expect(names(sortTitles(tied, 'date-asc'))).toEqual(['e1', 'e2', 'x', 'y'])
  })
})

describe('defaultSeriesSort', () => {
  it('is oldest first when any title has a date, else the current order', () => {
    expect(defaultSeriesSort([t('a', null), t('b', '2025-01-01')])).toBe('date-asc')
    expect(defaultSeriesSort([t('a', null), t('b', '')])).toBe('added')
    expect(defaultSeriesSort([])).toBe('added')
  })
})

describe('groupTitles', () => {
  const series = [{ id: 1, name: 'Saga' }, { id: 2, name: 'Alpha' }]

  it('puts titles with no series under "No series", last', () => {
    const groups = groupTitles([t('solo', null), t('s1', null, { series_id: 1 })], 'series', 'added', series)
    expect(groups.map((g) => g.label)).toEqual(['Saga', 'No series'])
  })
  it('shares a series between its titles, oldest first by default, with a count and media mix', () => {
    const groups = groupTitles([
      t('late', '2025-05-01', { series_id: 1 }), t('early', '2024-05-01', { series_id: 1, media_type: 'manga' }),
      t('undated', null, { series_id: 1 }),
    ], 'series', 'added', series)
    expect(groups).toHaveLength(1)
    expect(names(groups[0].items)).toEqual(['early', 'late', 'undated'])
    expect(groups[0].seriesId).toBe(1)
    expect(groups[0].mix).toBe('Anime ×2 · Manga ×1')
  })
  it('an explicit sort applies inside groups', () => {
    const groups = groupTitles([t('a', '2024-01-01', { series_id: 1 }), t('b', '2025-01-01', { series_id: 1 })], 'series', 'date-desc', series)
    expect(names(groups[0].items)).toEqual(['b', 'a'])
  })
  it('a title in a series the viewer cannot see is not attributed to it (private series)', () => {
    const groups = groupTitles([t('x', null, { series_id: 99 })], 'series', 'added', series)
    expect(groups.map((g) => g.label)).toEqual(['No series'])
  })
  it('has no empty groups: only the given (already filtered) titles make groups', () => {
    expect(groupTitles([], 'series', 'added', series)).toEqual([])
    expect(groupTitles([], 'none', 'added', series)).toEqual([])
    const filtered = [t('only', null, { series_id: 2 })]
    expect(groupTitles(filtered, 'series', 'added', series).map((g) => g.label)).toEqual(['Alpha'])
  })
  it('groups by media type and by status in pipeline order', () => {
    const items = [t('a', null, { status: 'exported' }), t('b', null, { status: 'aligned', media_type: 'manga' }), t('c', null, { status: null, media_type: null })]
    expect(groupTitles(items, 'status', 'added', []).map((g) => g.label)).toEqual(['Aligned', 'Exported', 'No status'])
    expect(groupTitles(items, 'media', 'added', []).map((g) => g.key)).toEqual(['media:anime', 'media:manga', 'media:'])
  })
  it('"none" is one ungrouped list in the chosen order', () => {
    const groups = groupTitles([t('b', '2025-01-01'), t('a', '2024-01-01')], 'none', 'date-asc', series)
    expect(groups).toHaveLength(1)
    expect(names(groups[0].items)).toEqual(['a', 'b'])
  })
})

describe('mediaMix', () => {
  it('counts per type, most first', () => {
    expect(mediaMix([{ media_type: 'manga' }, { media_type: 'anime' }, { media_type: 'anime' }, { media_type: null }]))
      .toBe('Anime ×2 · Manga ×1 · Unknown type ×1')
  })
})
