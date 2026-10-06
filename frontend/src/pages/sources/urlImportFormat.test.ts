import { describe, expect, it } from 'vitest'

import type { DramaSummary } from '../../api/types'
import type { SeriesChapter } from '../../types/sources'
import type { ChapterImportResult, ChapterSaveResult, ImportState, UrlPreview } from '../../types/sourcesImport'
import {
  MAX_CHAPTERS,
  allSelected,
  canTakeMedia,
  chapterImportDramas,
  chapterMarks,
  checkUrl,
  comicNote,
  contentTypeLabel,
  defaultAudioOnly,
  downloadReason,
  dramaLabel,
  importIds,
  importLabel,
  importReason,
  saveLabel,
  saveOutcomeText,
  saveOutcomeTone,
  saveReason,
  saveSummary,
  needsAiRows,
  newDramaRequest,
  outcomeSummary,
  outcomeText,
  outcomeTone,
  previewAction,
  previewFacts,
  retryIds,
  retryLabel,
  retryNote,
  selectAllLabel,
  selectableChapters,
  toggleId,
  urlImportText,
  videoDramas,
} from './urlImportFormat'

const preview = (over: Partial<UrlPreview> = {}): UrlPreview => ({
  kind: 'url_preview', content_type: 'novel', route: 'page', platform: 'Example', title: 'T', chapter: null,
  chapter_id: null, language: 'zh', chapter_count: null, adapter: null, series_id: null, text_length: null,
  image_count: null, notes: [], display_url: 'https://a.example/b', ...over,
})

const drama = (id: number, media_type: string | null, content_mode: string | null = null): DramaSummary => ({
  id, title_zh: null, title_en: `D${id}`, author: null, studio: null, director: null, voice_actors: null, status: null,
  source_language: 'zh', media_type, content_mode, series_id: null, translation_engine: null, custom_tags: [],
  created_at: null, updated_at: null,
})

const ch = (id: string): SeriesChapter => ({ chapter_id: id, title: id, group: '', url: '' })

describe('checkUrl', () => {
  it('accepts plain http(s) links', () => {
    expect(checkUrl(' https://a.example/b?c=1 ')).toBeNull()
    expect(checkUrl('http://a.example')).toBeNull()
  })
  it('explains what is wrong', () => {
    expect(checkUrl('')).toBe('Still needed: a link.')
    expect(checkUrl('ftp://a.example')).toMatch(/http:\/\/ or https:\/\//)
    expect(checkUrl('a.example/b')).toMatch(/http:\/\/ or https:\/\//)
    expect(checkUrl('https://a.example/b c')).toMatch(/http:\/\/ or https:\/\//)
    expect(checkUrl('https://user:pw@a.example/')).toBe('Links with a user name or password are not supported.')
    expect(checkUrl(`https://a.example/${'x'.repeat(2000)}`)).toMatch(/too long/)
  })
})

describe('preview card', () => {
  it('labels the type and lists the facts that are there', () => {
    expect(contentTypeLabel('video')).toBe('Video')
    expect(contentTypeLabel('weird')).toBe('Unknown')
    expect(contentTypeLabel(null)).toBe('Unknown')
    expect(previewFacts(preview({ chapter: 'Chapter 3', chapter_count: 120, text_length: 4200 }))).toEqual([
      'Example', 'Chapter 3', 'zh', '120 chapters', '4,200 characters',
    ])
    expect(previewFacts(preview({ platform: null, language: null, chapter_count: 1, image_count: 12 }))).toEqual([
      '1 chapter', '12 images',
    ])
  })
  it('picks the action per kind', () => {
    expect(previewAction(preview({ route: 'series', adapter: 'alpha', series_id: 's' }))).toBe('series')
    expect(previewAction(preview({ route: 'chapter', adapter: 'alpha', series_id: 's', content_type: 'comic' }))).toBe('series')
    // A series link with no adapter falls back to the content type.
    expect(previewAction(preview({ route: 'series', adapter: null, series_id: 's' }))).toBe('novel')
    expect(previewAction(preview({ content_type: 'video', route: 'video' }))).toBe('video')
    expect(previewAction(preview({ content_type: 'comic' }))).toBe('comic')
    expect(previewAction(preview({ content_type: 'unknown' }))).toBe('unknown')
  })
})

describe('drama pickers', () => {
  const list = [drama(1, 'audio_drama'), drama(2, 'novel'), drama(3, 'manhua'), drama(4, 'novel', 'novel_narration'), drama(5, 'streamer_vod', 'streamer_vod')]
  it('keeps only dramas the chapter import accepts', () => {
    expect(chapterImportDramas(list, true).map((d) => d.id)).toEqual([3])
    expect(chapterImportDramas(list, false).map((d) => d.id)).toEqual([2, 4])
  })
  it('video goes only into audio drama or streamer VOD modes', () => {
    expect(videoDramas(list).map((d) => d.id)).toEqual([1, 2, 3, 5])
    expect(canTakeMedia(null)).toBe(true)
    expect(canTakeMedia('novel_narration')).toBe(false)
    expect(defaultAudioOnly('streamer_vod')).toBe(false)
    expect(defaultAudioOnly('audio_drama')).toBe(true)
    expect(defaultAudioOnly(null)).toBe(true)
  })
  it('labels a drama by title', () => {
    expect(dramaLabel({ id: 9, title_en: '', title_zh: '天官' })).toBe('天官')
    expect(dramaLabel({ id: 9, title_en: null, title_zh: null })).toBe('Drama 9')
  })
  it('builds a New drama body the import accepts', () => {
    expect(newDramaRequest('天官赐福', 'zh', false)).toEqual({ source_language: 'zh', title_zh: '天官赐福', media_type: 'novel' })
    expect(newDramaRequest(' Solo ', 'ko', true)).toEqual({ source_language: 'ko', title_en: 'Solo', media_type: 'manhwa' })
    expect(newDramaRequest('X', 'ja-JP', true).media_type).toBe('manga')
    expect(newDramaRequest('X', 'en', true)).toMatchObject({ source_language: 'zh', media_type: 'manhua' })
  })
})

describe('chapter selection', () => {
  const chapters = [ch('c1'), ch('c2'), ch('c3')]
  it('toggles and selects all', () => {
    expect(toggleId([], 'c1', true)).toEqual(['c1'])
    expect(toggleId(['c1'], 'c1', true)).toEqual(['c1'])
    expect(toggleId(['c1', 'c2'], 'c1', false)).toEqual(['c2'])
    expect(allSelected(['c3', 'c1', 'c2'], chapters)).toBe(true)
    expect(allSelected(['c1'], chapters)).toBe(false)
    expect(allSelected([], [])).toBe(false)
  })
  it('sends ids only, in series order, dropping unknown ones', () => {
    expect(importIds(['c3', 'zz', 'c1'], chapters)).toEqual(['c1', 'c3'])
  })
  it('labels and gates the Import button', () => {
    expect(importLabel(1)).toBe('Import 1 chapter')
    expect(importLabel(3)).toBe('Import 3 chapters')
    expect(importReason(0, 1)).toBe('Still needed: at least one chapter.')
    expect(importReason(MAX_CHAPTERS + 1, 1)).toBe('Import at most 200 chapters at a time.')
    expect(importReason(2, null)).toBe('Still needed: a drama to import into.')
    expect(importReason(2, 4)).toBeNull()
  })
})

describe('outcomes', () => {
  const result: ChapterImportResult = {
    kind: 'chapter_import',
    chapters: [
      { chapter_id: 'c1', title: 'One', outcome: 'imported', pages: 12 },
      { chapter_id: 'c2', title: 'Two', outcome: 'imported', pages: 1 },
      { chapter_id: 'c3', title: 'Three', outcome: 'skipped' },
      { chapter_id: 'c4', title: 'Four', outcome: 'failed', error: 'The site timed out.' },
      { chapter_id: 'c5', title: 'Five', outcome: 'not_found' },
    ],
    imported_count: 2, skipped_count: 1, failed_count: 1, not_attempted_count: 0, retry_chapter_ids: ['c4'], partial: true,
    cancelled: false, handoff: null,
  }
  it('summarises and describes each chapter', () => {
    expect(outcomeSummary(result)).toBe('2 imported · 1 already there · 1 failed · 1 not found')
    expect(outcomeSummary({ ...result, cancelled: true, chapters: [], skipped_count: 0, failed_count: 0 })).toBe('Stopped. 2 imported')
    expect(result.chapters.map(outcomeText)).toEqual([
      'Imported · 12 pages', 'Imported · 1 page', 'Already imported', 'Failed: The site timed out.', 'No longer on the site',
    ])
    expect(outcomeText({ chapter_id: 'x', title: '', outcome: 'imported', chars: 3400 })).toBe('Imported · 3,400 characters')
  })
  it('shows chapters a stopped run never reached', () => {
    const stopped: ChapterImportResult = {
      ...result,
      chapters: [
        { chapter_id: 'c1', title: 'One', outcome: 'imported', chars: 10 },
        { chapter_id: 'c2', title: 'Two', outcome: 'not_attempted', error: 'Not attempted: the import stopped before this chapter.' },
        { chapter_id: 'c3', title: 'Three', outcome: 'not_attempted', error: 'Not attempted: the import stopped before this chapter.' },
      ],
      imported_count: 1, skipped_count: 0, failed_count: 0, not_attempted_count: 2, retry_chapter_ids: ['c2', 'c3'],
      cancelled: true,
    }
    expect(outcomeSummary(stopped)).toBe('Stopped. 1 imported · 2 not attempted')
    expect(outcomeText(stopped.chapters[1])).toBe('Not attempted')
    expect(outcomeTone('not_attempted')).toBe('warn')
    expect(outcomeTone('failed')).toBe('bad')
    expect(outcomeTone('imported')).toBe('ok')
    expect(outcomeTone('skipped')).toBe('muted')
  })
  it('never shows a path or key from a failure', () => {
    expect(outcomeText({ chapter_id: 'x', title: '', outcome: 'failed', error: 'open /home/kae/lib/x failed' })).toBe('Failed')
    expect(outcomeText({ chapter_id: 'x', title: '', outcome: 'failed', error: 'api_key=sk-abcdefgh' })).toBe('Failed')
  })
  it('explains comic pages have no viewer yet', () => {
    expect(comicNote(result)).toMatch(/^Imported 13 pages\. There’s no page viewer here yet/)
    expect(comicNote({ ...result, chapters: [{ chapter_id: 'a', title: '', outcome: 'imported', chars: 10 }] })).toBeNull()
  })
  it('url import copy', () => {
    expect(urlImportText({ kind: 'url_import', needs_review: false, char_count: 5120 })).toBe('Added 5,120 characters to the drama’s novel text.')
    expect(urlImportText({ kind: 'url_import', needs_review: true, char_count: 0 })).toMatch(/nothing was saved/)
  })
  it('url import copy after following next chapters', () => {
    const r = { kind: 'url_import' as const, needs_review: true, char_count: 9000, review_open: true, pages_found: 3, follow_stop: 'cap' }
    expect(urlImportText(r)).toBe('Read 3 pages. Nothing was saved yet. Check the pages below, then import the ones you want.')
    expect(urlImportText({ ...r, pages_found: 1 })).toMatch(/^Nothing was saved yet\. Check what Baihe found/)
  })
})

describe('downloadReason', () => {
  it('needs a good link, and the replace tick when there is audio', () => {
    expect(downloadReason('', false, false)).toBe('Still needed: a link.')
    expect(downloadReason('https://v.example/x', true, false)).toMatch(/Replace the current audio/)
    expect(downloadReason('https://v.example/x', true, true)).toBeNull()
    expect(downloadReason('https://v.example/x', false, false)).toBeNull()
  })
})

describe('import state (Step 107)', () => {
  const state = (over: Partial<ImportState> = {}): ImportState => ({
    source: 'alpha', series_id: 'a0', drama_id: 12, imported_chapter_ids: ['c1', 'c2'],
    retry: [
      { chapter_id: 'c3', title: 'Three', status: 'failed', error: 'The site took too long to answer.' },
      { chapter_id: 'c4', title: 'Four', status: 'not_attempted', error: 'Not attempted: the import stopped before this chapter.' },
      { chapter_id: 'c5', title: 'Five', status: 'failed', error: 'open /home/kae/lib/x failed' },
    ],
    retry_count: 3, ...over,
  })
  const chapters = ['c1', 'c2', 'c3', 'c4', 'c5'].map(ch)
  const done: ChapterImportResult = {
    kind: 'chapter_import', chapters: [], imported_count: 0, skipped_count: 0, failed_count: 1, not_attempted_count: 1,
    retry_chapter_ids: ['c9', 'c8'], partial: true, cancelled: false, handoff: null,
  }

  it('marks imported, failed and not attempted chapters, without paths', () => {
    const marks = chapterMarks(state())
    expect(marks.get('c1')).toEqual({ label: 'Imported', tone: 'ok', note: null })
    expect(marks.get('c3')).toEqual({ label: 'Failed', tone: 'bad', note: 'The site took too long to answer.' })
    expect(marks.get('c4')).toEqual({ label: 'Not attempted', tone: 'warn', note: null })
    expect(marks.get('c5')).toEqual({ label: 'Failed', tone: 'bad', note: null })
    expect(marks.has('c9')).toBe(false)
    expect(chapterMarks(null).size).toBe(0)
  })
  it('a chapter that needs AI help is marked, listed, and never retried automatically', () => {
    const s = state({
      retry: [
        ...state().retry,
        { chapter_id: 'c6', title: 'Six', status: 'needs_ai', error: 'Needs AI help: confirm.' },
      ],
    })
    expect(chapterMarks(s).get('c6')).toEqual({ label: 'Needs AI help', tone: 'warn', note: null })
    expect(retryIds(s, null)).toEqual(['c3', 'c4', 'c5'])
    expect(needsAiRows(s).map((r) => r.chapter_id)).toEqual(['c6'])
    expect(needsAiRows(null)).toEqual([])
    expect(outcomeText({ chapter_id: 'c6', title: 'Six', outcome: 'needs_ai' })).toBe('Needs AI help')
    expect(outcomeTone('needs_ai')).toBe('warn')
  })
  it('select all leaves out chapters already imported', () => {
    expect(selectableChapters(chapters, state()).map((c) => c.chapter_id)).toEqual(['c3', 'c4', 'c5'])
    expect(selectableChapters(chapters, null)).toBe(chapters)
    expect(selectAllLabel(3, 5)).toBe('Select all 3 not yet imported')
    expect(selectAllLabel(124, 124)).toBe('Select all 124')
  })
  it('retries the saved set, else the run that just finished', () => {
    expect(retryIds(state(), done)).toEqual(['c3', 'c4', 'c5'])
    expect(retryIds(state({ retry: [], retry_count: 0 }), done)).toEqual([])
    expect(retryIds(null, done)).toEqual(['c9', 'c8'])
    expect(retryIds(null, null)).toEqual([])
    expect(retryLabel(3)).toBe('Retry failed chapters (3)')
    expect(retryNote(3)).toBeNull()
    expect(retryNote(MAX_CHAPTERS + 50)).toBe('Retries the first 200 of 250; run it again for the rest.')
  })
  it('shows a partly imported chapter but never retries it automatically', () => {
    const partly = state({
      retry: [
        ...state().retry,
        { chapter_id: 'c6', title: 'Six', status: 'partial', error: 'It may be partly imported -- check the drama before retrying it.' },
      ],
    })
    expect(chapterMarks(partly).get('c6')).toEqual({
      label: 'Check first', tone: 'bad', note: 'It may be partly imported -- check the drama before retrying it.',
    })
    expect(retryIds(partly, done)).toEqual(['c3', 'c4', 'c5'])
  })
})

describe('save as CBZ', () => {
  const result = (over: Partial<ChapterSaveResult> = {}): ChapterSaveResult => ({
    kind: 'chapter_save',
    chapters: [
      { chapter_id: 'c1', title: 'One', outcome: 'saved', pages: 18 },
      { chapter_id: 'c2', title: 'Two', outcome: 'skipped' },
      { chapter_id: 'c3', title: 'Three', outcome: 'failed', error: 'site down' },
    ],
    saved_count: 1, skipped_count: 1, failed_count: 1, not_attempted_count: 0, not_found_count: 0,
    partial: true, cancelled: false, handoff: null,
    ...over,
  })
  it('labels the button and says why it is off', () => {
    expect(saveLabel(1)).toBe('Save 1 chapter as CBZ')
    expect(saveLabel(3)).toBe('Save 3 chapters as CBZ')
    expect(saveReason(0)).toBe('Still needed: at least one chapter.')
    expect(saveReason(MAX_CHAPTERS + 1)).toBe('Save at most 200 chapters at a time.')
    expect(saveReason(2)).toBeNull()
  })
  it('summarises and describes each chapter', () => {
    expect(saveSummary(result())).toBe('1 saved · 1 already saved · 1 failed')
    expect(saveSummary(result({ cancelled: true, not_attempted_count: 2 }))).toBe(
      'Stopped. 1 saved · 1 already saved · 1 failed · 2 not attempted',
    )
    const [saved, skipped, failed] = result().chapters
    expect(saveOutcomeText(saved)).toBe('Saved · 18 pages')
    expect(saveOutcomeText(skipped)).toBe('Already saved')
    expect(saveOutcomeText(failed)).toBe('Failed: site down')
    expect(saveOutcomeTone('saved')).toBe('ok')
    expect(saveOutcomeTone('failed')).toBe('bad')
  })
})
