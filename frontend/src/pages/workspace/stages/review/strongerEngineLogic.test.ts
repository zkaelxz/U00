import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../../api/client'
import type { StrongerEngineSuggestions } from '../../../../types/strongerEngine'
import {
  buildOffers,
  estimateText,
  formatUsd,
  reasonCaption,
  resultIsStale,
  tryErrorText,
  tryLabel,
} from './strongerEngineLogic'

const labels = {
  qc_flag: 'Flagged in review',
  glossary_conflict: "A glossary term isn't used",
  ambiguous_term: 'A term has more than one glossary translation',
}

const suggestions = (over: Partial<StrongerEngineSuggestions> = {}): StrongerEngineSuggestions => ({
  drama_id: 3,
  engine: 'claude',
  current_engine: 'test_offline',
  available: true,
  reason_labels: labels,
  lines: [
    { line_id: 7, reasons: ['qc_flag', 'glossary_conflict'], estimate_usd: 0.0012 },
    { line_id: 9, reasons: ['ambiguous_term'], estimate_usd: 0 },
  ],
  ...over,
})

const apiError = (status: number, message: string, code = 'x') => new ApiError(status, { code, message })

describe('stronger engine logic', () => {
  it('formats costs in plain words', () => {
    expect(formatUsd(0)).toBe('free')
    expect(formatUsd(Number.NaN)).toBe('free')
    expect(formatUsd(0.0004)).toBe('under $0.001')
    expect(formatUsd(0.0012)).toBe('$0.0012')
    expect(formatUsd(0.034)).toBe('$0.034')
    expect(formatUsd(1.5)).toBe('$1.50')
    expect(estimateText(0.0012)).toBe('~$0.0012')
    expect(estimateText(0.0004)).toBe('under $0.001')
    expect(estimateText(0)).toBe('free')
  })

  it('labels the button with the engine and the estimate', () => {
    expect(tryLabel({ engineLabel: 'Claude', estimateUsd: 0.0012 })).toBe('Try with Claude (~$0.0012)')
    expect(tryLabel({ engineLabel: 'Ollama', estimateUsd: 0 })).toBe('Try with Ollama (free)')
  })

  it('joins known reasons and never shows a raw key', () => {
    expect(reasonCaption(['qc_flag', 'glossary_conflict'], labels)).toBe("Flagged in review · A glossary term isn't used")
    expect(reasonCaption(['new_reason', 'qc_flag'], labels)).toBe('Flagged in review')
    expect(reasonCaption([], labels)).toBe('')
  })

  it('builds offers by line id with the humanized engine', () => {
    const m = buildOffers(suggestions())
    expect([...m.keys()]).toEqual([7, 9])
    expect(m.get(7)).toEqual({
      engine: 'claude',
      engineLabel: 'Claude',
      caption: "Flagged in review · A glossary term isn't used",
      estimateUsd: 0.0012,
    })
  })

  it('offers nothing when unavailable or not loaded', () => {
    expect(buildOffers(null).size).toBe(0)
    expect(buildOffers(suggestions({ available: false })).size).toBe(0)
  })

  it('a result is stale once the English changed (null counts as empty)', () => {
    expect(resultIsStale('Hello', 'Hello')).toBe(false)
    expect(resultIsStale(null, '')).toBe(false)
    expect(resultIsStale('Hello!', 'Hello')).toBe(true)
  })

  it('gives plain text for the known refusals', () => {
    expect(tryErrorText(apiError(400, 'This would go over your monthly spending cap.'), 'Claude'))
      .toBe('This would go over your monthly spending cap.')
    // A path-like server message is dropped for a fixed sentence.
    expect(tryErrorText(apiError(400, 'failed at /home/kae/x'), 'Claude')).toBe('Claude could not translate this line.')
    expect(tryErrorText(apiError(403, 'no'), 'Claude')).toMatch(/isn't allowed to use paid engines/)
    expect(tryErrorText(apiError(429, 'busy'), 'Claude')).toMatch(/Another AI request is running/)
    expect(tryErrorText(apiError(503, 'No claude key'), 'Claude')).toMatch(/Claude isn't set up/)
    expect(tryErrorText(apiError(500, 'boom'), 'Claude')).toBeNull()
    expect(tryErrorText(new Error('x'), 'Claude')).toBeNull()
  })
})
