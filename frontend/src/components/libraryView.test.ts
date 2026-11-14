import { describe, expect, it } from 'vitest'

import {
  cardProgress, continueAction, continueItems, countDramas, hideLibraryBar, needsCheckCount, parseTime, readAction, readHref, readVerb,
  shownTags, tileText, workspaceHref, type ContinueItem, type ShelfRow,
} from './libraryView'

const t = (title_en: string | null, title_zh: string | null) => ({ title_en, title_zh })

describe('tileText', () => {
  it('prefers the first CJK character of the original title', () => {
    expect(tileText(t('Signal', '시그널'))).toBe('시')
    expect(tileText(t('Moonlit', 'Vol. 2 月光'))).toBe('月')
  })
  it('falls back to English initials, then "?"', () => {
    expect(tileText(t("heaven official's blessing", null))).toBe('HO')
    expect(tileText(t(null, null))).toBe('?')
  })
})

describe('shownTags', () => {
  it('caps the tags and counts the rest', () => {
    expect(shownTags(['a', 'b', 'c'])).toEqual({ shown: ['a', 'b'], more: 1 })
    expect(shownTags([])).toEqual({ shown: [], more: 0 })
  })
})

describe('parseTime', () => {
  it('reads naive timestamps as UTC and tolerates junk', () => {
    expect(parseTime('2026-09-29T10:00:00')).toBe(Date.UTC(2026, 8, 29, 10))
    expect(parseTime('2026-09-29T10:00:00+00:00')).toBe(Date.UTC(2026, 8, 29, 10))
    expect(parseTime(null)).toBe(0)
    expect(parseTime('nope')).toBe(0)
  })
})

describe('continueItems', () => {
  const read = (drama_id: number, last_accessed_at: string, percent_complete: number | null = 40) =>
    ({ drama_id, last_accessed_at, percent_complete, last_page: 2, has_cover_art: false, title_en: `D${drama_id}`, title_zh: null })
  const work = (id: number, updated_at: string) =>
    ({ id, updated_at, status: 'aligned', media_type: null, title_en: `D${id}`, title_zh: null })

  it('merges reading and workspace activity newest first, one entry per title', () => {
    const items = continueItems(
      [read(1, '2026-09-29T12:00:00'), read(1, '2026-09-29T11:00:00'), read(2, '2026-09-28T09:00:00')],
      [work(2, '2026-09-29T10:00:00'), work(3, '2026-09-27T10:00:00')],
    )
    expect(items.map((x) => `${x.kind}-${x.dramaId}`)).toEqual(['read-1', 'work-2', 'work-3'])
    expect(items[0]).toMatchObject({ page: 2, percent: 40, cover: false })
  })

  it('is empty when there is nothing to resume', () => {
    expect(continueItems([], [])).toEqual([])
  })
})

describe('small helpers', () => {
  it('counts titles in plain English', () => {
    expect(countDramas(1)).toBe('1 title')
    expect(countDramas(3)).toBe('3 titles')
  })
  it('sends comics to the comic reader', () => {
    expect(readHref({ id: 4, media_type: 'manga' })).toBe('#/comic/4')
    expect(readHref({ id: 4, media_type: 'novel' })).toBe('#/read/4')
  })
  it('opens a title on its current stage unless a stage is given', () => {
    expect(workspaceHref(7)).toBe('#/drama/7')
    expect(workspaceHref(7, 'export')).toBe('#/drama/7/export')
  })
})

describe('hideLibraryBar', () => {
  it('hides the bar at 0 titles and shows it from 1, while loading, and when a filter matches nothing', () => {
    expect(hideLibraryBar(0, false)).toBe(true)
    expect(hideLibraryBar(1, false)).toBe(false)
    expect(hideLibraryBar(null, false)).toBe(false)
    expect(hideLibraryBar(0, true)).toBe(false)
  })
})

describe('cardProgress for comics', () => {
  it('counts translated pages once translation has started', () => {
    expect(cardProgress({ status: null, page_count: 12, pages_translated: 4 })).toEqual({ text: 'Translated 4 of 12 pages', share: 4 / 12 })
  })
  it('shows nothing for a comic nobody has translated', () => {
    expect(cardProgress({ status: null, page_count: 12, pages_translated: 0 })).toBeNull()
  })
})

describe('readVerb and readAction', () => {
  it('says Watch for video, Listen for audio and Read for the rest', () => {
    expect(readVerb({ media_type: 'video_drama' })).toBe('Watch')
    expect(readVerb({ media_type: 'anime' })).toBe('Watch')
    expect(readVerb({ media_type: 'streamer_vod' })).toBe('Watch')
    expect(readVerb({ media_type: 'audio_drama' })).toBe('Listen')
    expect(readVerb({ media_type: 'asmr' })).toBe('Listen')
    expect(readVerb({ media_type: 'novel' })).toBe('Read')
    expect(readVerb({ media_type: 'manga' })).toBe('Read')
    expect(readVerb({ media_type: null })).toBe('Read')
  })
  it('reads a narrated novel whatever its media type says', () => {
    expect(readVerb({ media_type: 'audio_drama', content_mode: 'novel_narration' })).toBe('Read')
    expect(readVerb({ media_type: 'video_drama', content_mode: 'novel_narration' })).toBe('Read')
  })
  it('opens the Watch page only from Watch and Listen', () => {
    expect(readAction({ id: 3, media_type: 'video_drama' })).toEqual({ label: 'Watch', href: '#/watch/3' })
    expect(readAction({ id: 1, media_type: 'audio_drama' })).toEqual({ label: 'Listen', href: '#/watch/1' })
    expect(readAction({ id: 2, media_type: 'novel' })).toEqual({ label: 'Read', href: '#/read/2' })
    expect(readAction({ id: 4, media_type: 'manga' })).toEqual({ label: 'Read', href: '#/comic/4' })
    expect(readAction({ id: 5, media_type: null })).toEqual({ label: 'Read', href: '#/read/5' })
  })
  it('says Continue, opening the Reader, once the title has a saved reading place', () => {
    expect(readAction({ id: 3, media_type: 'video_drama' }, true)).toEqual({ label: 'Continue', href: '#/read/3' })
  })
})

describe('continueAction', () => {
  const reading = (page: number | null): ContinueItem =>
    ({ kind: 'read', dramaId: 2, title: 'Blessing', titleZh: null, percent: 40, page, cover: false, at: 0 })
  const work = (mediaType: string | null = 'video_drama'): ContinueItem =>
    ({ kind: 'work', dramaId: 5, title: 'Signal', titleZh: null, status: 'aligned', mediaType, at: 0 })
  const row = (over: Partial<ShelfRow> = {}): ShelfRow =>
    ({ id: 5, media_type: 'video_drama', content_mode: null, status: 'aligned', line_count: 10, translated_count: 5, ...over })
  const done = (over: Partial<ShelfRow> = {}) => row({ translated_count: 10, ...over })

  it('reading tiles: the saved page above 1, else Continue reading; the link is unchanged', () => {
    expect(continueAction(reading(12), undefined, null)).toEqual({ label: 'Read from page 12', href: '#/read/2' })
    expect(continueAction(reading(1), undefined, null)).toEqual({ label: 'Continue reading', href: '#/read/2' })
    expect(continueAction(reading(null), undefined, null)).toEqual({ label: 'Continue reading', href: '#/read/2' })
    expect(continueAction(reading(3), row({ id: 2, media_type: 'manga' }), null)).toEqual({ label: 'Read from page 3', href: '#/comic/2' })
  })

  it('Open on the work page for a comic, a title not in the loaded list, and a title with no lines', () => {
    expect(continueAction(work('manga'), row({ media_type: 'manga' }), null)).toEqual({ label: 'Open', href: '#/drama/5' })
    expect(continueAction(work('manhwa'), undefined, null)).toEqual({ label: 'Open', href: '#/drama/5' })
    expect(continueAction(work(), undefined, null)).toEqual({ label: 'Open', href: '#/drama/5' })
    expect(continueAction(work(), row({ line_count: 0, translated_count: 0 }), null)).toEqual({ label: 'Open', href: '#/drama/5' })
  })

  it('Translate N lines on the Translate step while lines have no English, singular and with thousands', () => {
    expect(continueAction(work(), row(), null)).toEqual({ label: 'Translate 5 lines', href: '#/drama/5/translate' })
    expect(continueAction(work(), row({ translated_count: 9 }), 3)).toEqual({ label: 'Translate 1 line', href: '#/drama/5/translate' })
    expect(continueAction(work(), row({ line_count: 2000, translated_count: 766 }), null).label).toBe('Translate 1,234 lines')
    // Lines still waiting win over an exported status.
    expect(continueAction(work(), row({ status: 'exported' }), null).label).toBe('Translate 5 lines')
  })

  it('Check the N lines on the Review step once every line has English, worded as the title page words it', () => {
    expect(continueAction(work(), done(), 3)).toEqual({ label: 'Check the 3 lines', href: '#/drama/5/review' })
    expect(continueAction(work(), done(), 1)).toEqual({ label: 'Check the line', href: '#/drama/5/review' })
    expect(continueAction(work(), done(), 1500).label).toBe('Check the 1500 lines')
  })

  it('Check the lines while the count is unknown (loading or failed), Export when nothing needs a look', () => {
    expect(continueAction(work(), done(), null)).toEqual({ label: 'Check the lines', href: '#/drama/5/review' })
    expect(continueAction(work(), done(), 0)).toEqual({ label: 'Export', href: '#/drama/5/export' })
  })

  it('an exported title says Watch, Listen or Read and opens the same page as the cards do', () => {
    expect(continueAction(work(), done({ status: 'exported' }), null)).toEqual({ label: 'Watch', href: '#/watch/5' })
    expect(continueAction(work('audio_drama'), done({ status: 'exported', media_type: 'audio_drama' }), null))
      .toEqual({ label: 'Listen', href: '#/watch/5' })
    expect(continueAction(work('novel'), done({ status: 'exported', media_type: 'novel' }), 4)).toEqual({ label: 'Read', href: '#/read/5' })
    expect(continueAction(work('audio_drama'), done({ status: 'exported', media_type: 'audio_drama', content_mode: 'novel_narration' }), null))
      .toEqual({ label: 'Read', href: '#/read/5' })
  })

  it('asks for the count only for a work tile in the check state', () => {
    expect(needsCheckCount(work(), done())).toBe(true)
    expect(needsCheckCount(work(), row())).toBe(false)
    expect(needsCheckCount(work(), done({ status: 'exported' }))).toBe(false)
    expect(needsCheckCount(work(), undefined)).toBe(false)
    expect(needsCheckCount(work('manga'), done({ media_type: 'manga' }))).toBe(false)
    expect(needsCheckCount(reading(2), done({ id: 2 }))).toBe(false)
  })
})
