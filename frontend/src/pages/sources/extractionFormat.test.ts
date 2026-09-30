import { describe, expect, it } from 'vitest'

import type { ExtractionImage, ExtractionNovel, ExtractionReview } from '../../types/sourcesExtraction'
import {
  AI_OFF, aiReason, aiRequestFields, canImport, changedImages, comicImportText, duplicatePages, effectiveEngine, engineLabel,
  exclusionsFor, importLabel, nextPageNumber, novelForm, profileSavedText, reviewImportText, reviewWhy, roleLabel,
  skippedTitle, withContainer,
} from './extractionFormat'

const engines = { engines: ['claude', 'gemini', 'ollama'], default: 'claude' }

describe('AI fallback choice', () => {
  it('sends nothing while off', () => {
    expect(aiRequestFields(AI_OFF, engines)).toEqual({})
    expect(effectiveEngine({ on: false, engine: 'gemini' }, engines)).toBeNull()
    expect(aiReason(AI_OFF, engines)).toBeNull()
    expect(aiReason(AI_OFF, null)).toBeNull()
  })

  it('waits for the engine list once on', () => {
    expect(aiReason({ on: true, engine: null }, null)).toMatch(/Loading/)
  })

  it('uses the saved default until an engine is picked', () => {
    expect(aiRequestFields({ on: true, engine: null }, engines)).toEqual({ use_ai: true, engine: 'claude' })
    expect(aiRequestFields({ on: true, engine: 'ollama' }, engines)).toEqual({ use_ai: true, engine: 'ollama' })
  })

  it('ignores a picked engine the server no longer offers', () => {
    expect(effectiveEngine({ on: true, engine: 'deepl' }, engines)).toBe('claude')
  })

  it('asks for an engine when there is no usable default', () => {
    const none = { engines: ['gemini'], default: null }
    expect(aiReason({ on: true, engine: null }, none)).toBe('Still needed: an AI engine.')
    expect(aiRequestFields({ on: true, engine: null }, none)).toEqual({})
    expect(aiReason({ on: true, engine: null }, { engines: [], default: null })).toMatch(/No AI engine/)
  })

  it('labels engines', () => {
    expect(engineLabel('ollama')).toBe('Ollama (on this PC)')
    expect(engineLabel('claude')).toBeTruthy()
  })
})

describe('comic import result', () => {
  const base = { kind: 'comic_import' as const, needs_review: false, pages_added: 12, skipped: [], skipped_count: 0 }

  it('says how many pages were added, or that nothing was', () => {
    expect(comicImportText(base)).toBe('Added 12 pages to the drama.')
    expect(comicImportText({ ...base, pages_added: 1 })).toBe('Added 1 page to the drama.')
    expect(comicImportText({ ...base, needs_review: true, pages_added: 0 })).toMatch(/nothing was added/)
  })

  it('titles the left-out list, noting when it is cut short', () => {
    expect(skippedTitle(base)).toBeNull()
    const one = { display_url: 'https://a.example/i.png', reason: 'too small' }
    expect(skippedTitle({ ...base, skipped: [one], skipped_count: 1 })).toBe('1 image left out')
    expect(skippedTitle({ ...base, skipped: [one], skipped_count: 150 })).toBe('150 images left out (first 1 shown)')
  })
})

const novel: ExtractionNovel = {
  text_preview: 'abc', char_count: 300, chapter_title: 'Ch 1',
  containers: [
    { selector: '#content', chars: 300, preview: 'Chapter text…', exclusions: [{ selector: '.ad', preview: 'Ad' }] },
    { selector: 'body', chars: 900, preview: 'Everything', exclusions: [{ selector: '.nav', preview: 'Home' }] },
  ],
  content_selector: '#content', exclude_selectors: ['.ad'], headings: [{ id: 'b1', text: 'Ch 1' }], title_block: 'b1',
  links: [{ id: 'L1', text: 'Next', url: 'https://a.example/2' }], next_link: 'L1', previous_link: null, number_from: 'title',
}

const img = (id: number, role: string, page: number): ExtractionImage => ({
  id, display_url: `https://a.example/${id}.png`, attr: 'src', width: 800, height: 1200, role, page, reason: '', has_image: true,
})

describe('review: novel corrections', () => {
  it('starts from what the server shows', () => {
    expect(novelForm(novel)).toEqual({
      content_selector: '#content', exclude_selectors: ['.ad'], title_block: 'b1', next_link: 'L1', previous_link: null, number_from: 'title',
    })
    expect(novelForm({ ...novel, content_selector: null }).content_selector).toBe('#content')
  })

  it('drops leave-outs that do not belong to a new container', () => {
    const f = withContainer(novelForm(novel), novel, 'body')
    expect(f.content_selector).toBe('body')
    expect(f.exclude_selectors).toEqual([])
    expect(exclusionsFor(novel, 'body')).toEqual([{ selector: '.nav', preview: 'Home' }])
    expect(exclusionsFor(novel, 'nope')).toEqual([])
  })
})

describe('review: comic corrections', () => {
  const images = [img(0, 'icon', 0), img(1, 'content', 1), img(2, 'content', 2)]

  it('sends only what changed, keyed by image id', () => {
    expect(changedImages(images, {})).toEqual([])
    expect(changedImages(images, { 1: { role: 'content', page: 1 } })).toEqual([])
    expect(changedImages(images, { 2: { role: 'ad', page: 5 }, 0: { role: 'content', page: 3 } })).toEqual([
      { id: 0, role: 'content', page: 3 }, { id: 2, role: 'ad', page: 0 },
    ])
  })

  it('finds repeated page numbers and the next free one', () => {
    expect(duplicatePages(images, {})).toEqual([])
    expect(duplicatePages(images, { 2: { role: 'content', page: 1 } })).toEqual([1])
    expect(nextPageNumber(images, {})).toBe(3)
    expect(nextPageNumber(images, { 2: { role: 'ad', page: 0 } })).toBe(2)
  })

  it('labels roles', () => {
    expect(roleLabel('content')).toBe('Page')
    expect(roleLabel('audio_track')).toBe('Audio track')
  })
})

describe('review: import and messages', () => {
  const base: Omit<ExtractionReview, 'content_type' | 'novel' | 'comic'> = {
    kind: 'extraction_review', drama_id: 1, revision: 'r', why: 'asked', display_url: null,
    confidence: { overall: { bucket: 'LOW', score: 0.3 }, fields: [] },
    report: { headline: '', lines: [], llm_calls: 0, cache_hit: false, profile: '', pending_profile: null },
    can_save_profile: false,
  }
  const novelReview: ExtractionReview = { ...base, content_type: 'novel', novel, comic: null }
  const comicReview: ExtractionReview = {
    ...base, content_type: 'comic', novel: null, comic: { images: [], roles: ['content'], page_count: 0 },
  }

  it('labels and gates the import', () => {
    expect(importLabel(novelReview)).toBe('Import this text')
    expect(canImport(novelReview)).toBe(true)
    expect(importLabel({ ...comicReview, comic: { images: [], roles: [], page_count: 2 } })).toBe('Import 2 pages')
    expect(canImport(comicReview)).toBe(false)
  })

  it('explains why a review is open', () => {
    expect(reviewWhy('asked')).toMatch(/You asked/)
    expect(reviewWhy('diagnostics')).toMatch(/diagnostics mode/)
    expect(reviewWhy('something-new')).toMatch(/isn’t sure/)
  })

  it('reports the import and a saved profile', () => {
    expect(reviewImportText({ kind: 'review_import', content_type: 'novel', char_count: 1200 })).toBe(
      'Added 1,200 characters to the drama’s novel text.',
    )
    expect(reviewImportText({ kind: 'review_import', content_type: 'comic', pages_added: 1 })).toBe('Added 1 page to the drama.')
    expect(reviewImportText({ kind: 'review_import', content_type: 'comic', pages_added: 3, skipped: [], skipped_count: 2 })).toBe(
      'Added 3 pages to the drama. 2 images skipped (over a size limit or not PNG, JPEG or WebP).',
    )
    expect(profileSavedText({ domain: 'a.example', kind: 'novel', version: 2, replaces: 1 })).toMatch(/v2 for a\.example.*v1 is kept/)
    expect(profileSavedText({ domain: 'a.example', kind: 'novel', version: 1, replaces: null })).toMatch(/uses it\.$/)
  })
})
