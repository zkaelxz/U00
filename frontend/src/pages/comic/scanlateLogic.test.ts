import { describe, expect, it } from 'vitest'

import type { ScanlateConfig } from '../../types/scanlate'
import {
  checkFiles,
  defaultEngine,
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
