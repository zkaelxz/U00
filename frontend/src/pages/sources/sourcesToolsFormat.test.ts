import { describe, expect, it } from 'vitest'

import type { MediaResource, SourceExtraction, UrlPreflight } from '../../types/sourcesTools'
import {
  extractionAccess,
  extractionMeta,
  pastedHtmlProblem,
  pastedListingProblem,
  playableResources,
  preflightFacts,
  preflightTone,
  resourceLabel,
  whenText,
} from './sourcesToolsFormat'

const pf = (o: Partial<UrlPreflight> = {}): UrlPreflight => ({
  kind: 'url_preflight', ok: true, verdict: 'Looks importable as a novel.', permitted: true, reachable: true,
  content_type: 'novel', tier: 'STATIC_HTTP', adapter: null, title: '', text_chars: 1200, images: 0,
  confidence: 'HIGH', warnings: [], lines: [], display_url: 'https://a.example/b', ...o,
})

const res = (o: Partial<MediaResource>): MediaResource => ({
  index: 0, kind: 'video', role: 'unrelated', language: null, label: null, display_url: 'https://c.example/a.mp4',
  downloadable: true, ...o,
})

describe('pasted input checks', () => {
  it('html: empty and over 5 MB', () => {
    expect(pastedHtmlProblem('  ')).toMatch(/Paste/)
    expect(pastedHtmlProblem('<html>')).toBeNull()
    expect(pastedHtmlProblem('白'.repeat(1_700_000))).toMatch(/5 MB/)
  })
  it('listing: empty and too long', () => {
    expect(pastedListingProblem('')).toMatch(/Paste/)
    expect(pastedListingProblem('x')).toBeNull()
    expect(pastedListingProblem('x'.repeat(200_001))).toMatch(/200,000/)
  })
})

describe('site check', () => {
  it('tone', () => {
    expect(preflightTone(pf())).toBe('ok')
    expect(preflightTone(pf({ warnings: ['translated'] }))).toBe('warn')
    expect(preflightTone(pf({ ok: false }))).toBe('warn')
    expect(preflightTone(pf({ ok: false, permitted: false }))).toBe('danger')
  })
  it('facts', () => {
    expect(preflightFacts(pf())).toEqual(['Novel', 'via static http', '1,200 characters', 'confidence high'])
    expect(preflightFacts(pf({ content_type: 'unknown', tier: '', text_chars: 0, images: 1, confidence: '' }))).toEqual(['1 image'])
  })
})

describe('identify media', () => {
  it('labels and puts the main resource first, downloadable only', () => {
    const list = [
      res({ index: 0, role: 'trailer' }),
      res({ index: 1, role: 'main', label: '1080p' }),
      res({ index: 2, kind: 'subtitle', role: 'subtitle', language: 'zh', downloadable: false }),
    ]
    expect(playableResources(list).map((r) => r.index)).toEqual([1, 0])
    expect(resourceLabel(list[1])).toBe('Main · video · 1080p')
    expect(resourceLabel(list[2])).toBe('Subtitle · subtitle · zh')
  })
})

describe('extractions', () => {
  const row: SourceExtraction = {
    url: 'https://a.example/b', created_at: 1000, content_type: 'novel', headline: 'Worked', tier: null,
    extraction_tier: 'deterministic', llm_calls: 1, cache_hit: true, profile: '', confidence: 'HIGH',
    access: { authentication: 'none', entitlement: null, technical_protection: 'none', protection_detail: ['x'] },
    resource_types: [], reason: '', lines: [],
  }
  it('meta and access lines', () => {
    expect(extractionMeta(row)).toBe('Novel · AI calls: 1 (cached result reused) · confidence high')
    expect(extractionAccess(row)).toEqual([
      'Authentication: none',
      'Entitlement/purchase: unknown',
      'Resource types found: none identified',
      'Technical protection: none',
      'x',
    ])
    expect(extractionAccess({ ...row, access: null })).toEqual([])
  })
  it('when', () => {
    expect(whenText(null)).toBe('')
    expect(whenText(1000, 1000_000 + 20_000)).toBe('just now')
    expect(whenText(1000, 1000_000 + 5 * 60_000)).toBe('5 min ago')
    expect(whenText(1000, 1000_000 + 3 * 3_600_000)).toBe('3 h ago')
  })
})
