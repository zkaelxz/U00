import { describe, expect, it } from 'vitest'

import type { ScanlateConfig } from '../../types/scanlate'
import {
  checkFiles,
  describeWarning,
  neighbourWarning,
  warningPages,
  defaultEngine,
  firstPageNote,
  pagesText,
  parsePageRanges,
  progressSummary,
  runBlockedReason,
  toolsSummary,
  uploadResultText,
  usableEngines,
} from './scanlateLogic'

const limits = {
  image_types: ['png', 'jpg', 'jpeg', 'webp'],
  pdf: true,
  max_image_mb: 30,
  max_image_megapixels: 100,
  max_pdf_mb: 300,
  max_pdf_pages: 500,
  max_files: 3,
  max_total_mb: 50,
  strip_slice_ratio: 3,
  slice_strips_default: true,
}

function config(over: Partial<ScanlateConfig> = {}): ScanlateConfig {
  return {
    drama_id: 1,
    source_language: 'ja',
    engines: [
      { name: 'claude', label: 'Claude', free: false, key_configured: false },
      { name: 'ollama', label: 'Ollama', free: true, key_configured: true },
      { name: 'deepseek', label: 'DeepSeek', free: false, key_configured: true },
    ],
    default_engine: 'claude',
    detect_backends: ['auto', 'cv', 'ml'],
    ml_weights_cached: false,
    lama_weights_cached: true,
    ocr_backend: 'manga_ocr',
    ocr_backend_installed: false,
    art_captions_available: false,
    page_count: 4,
    pages_with_regions: 2,
    pages_rendered: 1,
    job_id: 'scanlate_1',
    job_running: false,
    upload_limits: limits,
    ...over,
  }
}

const MB = 1024 * 1024

describe('scanlate panel logic', () => {
  it('lists only engines with a key and falls back from an unusable default', () => {
    expect(usableEngines(config()).map((e) => e.name)).toEqual(['ollama', 'deepseek'])
    expect(defaultEngine(config())).toBe('ollama')
    expect(defaultEngine(config({ default_engine: 'deepseek' }))).toBe('deepseek')
    expect(defaultEngine(config({ engines: [] }))).toBe('')
    expect(defaultEngine(null)).toBe('')
  })

  it('names what blocks a run', () => {
    expect(runBlockedReason(config(), 'ollama')).toBeNull()
    expect(runBlockedReason(config({ page_count: 0 }), 'ollama')).toBe('Upload pages first.')
    expect(runBlockedReason(config(), '')).toMatch(/Settings/)
    expect(runBlockedReason(null, '')).toBeNull()
  })

  it('summarises tools and progress', () => {
    expect(toolsSummary(config())).toBe('OpenCV detector (ML model not downloaded) · LaMa cleanup · OCR: manga_ocr (not installed)')
    expect(progressSummary(config())).toBe('4 pages · 2 with text · 1 typeset')
    expect(progressSummary(config({ page_count: 0 }))).toBe('No pages yet')
    expect(progressSummary(config({ page_count: 1 }))).toBe('1 page · 2 with text · 1 typeset')
    expect(pagesText(1)).toBe('1 page')
    expect(pagesText(2)).toBe('2 pages')
  })

  it('checks files against the limits before sending', () => {
    const f = (name: string, mb: number) => ({ name, size: Math.round(mb * MB) })
    expect(checkFiles([], limits)).toMatch(/at least one/)
    expect(checkFiles([f('a.png', 1), f('b.JPG', 1), f('c.webp', 1)], limits)).toBeNull()
    expect(checkFiles([f('a.png', 1), f('b.png', 1), f('c.png', 1), f('d.png', 1)], limits)).toMatch(/At most 3/)
    expect(checkFiles([f('a.gif', 1)], limits)).toMatch(/PNG, JPEG, WebP or PDF/)
    expect(checkFiles([f('a.png', 31)], limits)).toMatch(/over 30 MB/)
    expect(checkFiles([f('a.pdf', 31)], { ...limits, max_total_mb: 1000 })).toBeNull()
    expect(checkFiles([f('a.pdf', 20), f('b.pdf', 20), f('c.pdf', 20)], limits)).toMatch(/in total/)
    expect(checkFiles([{ name: 'a.png', size: 0 }], limits)).toMatch(/empty/)
  })

  it('describes an upload result', () => {
    expect(uploadResultText({ added: 1, pdf_pages_skipped: 0, strips_sliced: 0 })).toBe('Added 1 page.')
    expect(uploadResultText({ added: 5, pdf_pages_skipped: 2, strips_sliced: 1 })).toBe(
      'Added 5 pages. 1 tall strip was sliced. 2 PDF pages had no image and were skipped.',
    )
  })
})

describe('PDF import without pypdf', () => {
  it('refuses a PDF with the reason when the server cannot read PDFs', () => {
    expect(checkFiles([{ name: 'a.pdf', size: 1024 }], { ...limits, pdf: false })).toMatch(/pypdf/)
    expect(checkFiles([{ name: 'a.png', size: 1024 }], { ...limits, pdf: false })).toBeNull()
  })
})

describe('firstPageNote', () => {
  const job = (status: string, message: string) => ({ status, message }) as never

  it('explains a slow first page while a run is on page 1', () => {
    expect(firstPageNote('run', job('running', 'Page 1 of 181'))).toMatch(/load.*models/)
  })

  it('says nothing later, after the run, or for an export', () => {
    expect(firstPageNote('run', job('running', 'Page 2 of 181'))).toBeUndefined()
    expect(firstPageNote('run', job('done', 'Page 1 of 1'))).toBeUndefined()
    expect(firstPageNote('export', job('running', 'Page 1 of 181'))).toBeUndefined()
    expect(firstPageNote('run', null)).toBeUndefined()
  })
})

describe('parsePageRanges', () => {
  it('reads numbers and ranges, sorted and without repeats', () => {
    expect(parsePageRanges('5-7, 2, 6', 20)).toEqual([2, 5, 6, 7])
    expect(parsePageRanges(' 3 ', 3)).toEqual([3])
  })
  it('refuses empty text, out-of-range pages, backwards ranges and junk', () => {
    for (const bad of ['', ' ', '0', '21', '7-5', '1-21', 'a', '1,,2', '1-']) {
      expect(parsePageRanges(bad, 20)).toBeNull()
    }
  })
})

describe('warning pages', () => {
  const n = (page_id: number, ordinal: number, message: string, level = 'warning') => ({ page_id, ordinal, notes: [{ level, message }] })
  const notes = [
    n(3, 12, 'Region 2 had no translation and was left as the original.'),
    n(1, 10, 'OCR found no text on this page, so nothing was saved.', 'error'),
    n(2, 11, 'No bubbles found on this page. That is normal for covers and splash art; otherwise try the ML detector.'),
    n(4, 15, 'Regions 1, 2 and 4 had no translation and were left as the original.'),
    { page_id: 5, ordinal: 16, notes: [{ level: 'info', message: 'Time: 1s' }] },
  ]

  it('names a text box by the number drawn on the page', () => {
    expect(describeWarning('Region 2 had no translation and was left as the original.')).toEqual({
      kind: 'untranslated',
      boxes: [2],
      text: 'Text box numbered 2 on the page has no translation and still shows the original.',
    })
    const many = describeWarning('Regions 1, 2 and 4 had no translation and were left as the original.')
    expect(many.text).toContain('Text boxes numbered 1, 2, 4')
    expect(many.boxes).toEqual([1, 2, 4])
  })

  it('groups by kind, most urgent first, in reading order, skipping info-only pages', () => {
    const { all, groups } = warningPages(notes)
    expect(all.map((w) => w.ordinal)).toEqual([10, 11, 12, 15])
    expect(groups.map((g) => [g.kind, g.pages.length, g.harmless])).toEqual([
      ['noText', 1, false],
      ['untranslated', 2, true],
      ['noBubbles', 1, true],
    ])
  })

  it('finds the next and previous warning page', () => {
    const { all } = warningPages(notes)
    expect(neighbourWarning(all, 11, 1)?.ordinal).toBe(12)
    expect(neighbourWarning(all, 11, -1)?.ordinal).toBe(10)
    expect(neighbourWarning(all, 15, 1)).toBeNull()
    expect(neighbourWarning(all, null, 1)?.ordinal).toBe(10)
  })
})
