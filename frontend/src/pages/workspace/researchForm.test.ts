import { describe, expect, it } from 'vitest'

import type { ResearchBudget, ResearchField } from '../../types/research'
import {
  budgetLine, choicesFor, confidenceLabel, costLine, defaultChoices, effectiveChoices, googleSearchUrl, hostOf,
  seenValues,
} from './researchForm'

const row = (field: string, status: ResearchField['status']): ResearchField =>
  ({ field, value: 'v', status, sources: [], current: status === 'new' ? null : 'c' })

const budget: ResearchBudget = {
  free_daily_limit: 500, used_today: 3, free_monthly_limit: 5000, used_this_month: 3,
  free_remaining: 497, paid_price_per_search_usd: 0.035,
  free_tier_key: false, key_configured: true, monthly_cap_usd: 0, month_spend_usd: 0,
  models: ['gemini-flash-lite-latest'], modes: ['quick', 'deep', 'verify'],
  estimates_usd: { quick: { 'gemini-flash-lite-latest': 0.0022 } },
}

describe('researchForm', () => {
  it('always offers keep/replace/save both for a conflict', () => {
    expect(choicesFor(row('title_en', 'conflict')).map((c) => c.value)).toEqual(['keep', 'replace', 'save_both'])
    expect(choicesFor(row('studio', 'new')).map((c) => c.value)).toEqual(['keep', 'replace'])
    expect(choicesFor(row('author', 'same')).map((c) => c.value)).toEqual(['keep', 'confirm'])
  })

  it('never pre-chooses an overwrite, and sends the values that were shown', () => {
    const rows = [row('title_en', 'conflict'), row('studio', 'new'), row('author', 'same')]
    const d = defaultChoices(rows)
    expect(d).toEqual({ title_en: 'keep', studio: 'keep', author: 'keep' })
    expect(effectiveChoices(d)).toEqual({})
    const picked = { ...d, studio: 'replace' as const, title_en: 'save_both' as const }
    expect(effectiveChoices(picked)).toEqual({ studio: 'replace', title_en: 'save_both' })
    expect(seenValues(rows, picked)).toEqual({ studio: null, title_en: 'c' })
  })

  it('labels confidence, budget and cost', () => {
    expect(confidenceLabel(0.9)).toBe('high confidence')
    expect(confidenceLabel(0.6)).toBe('medium confidence')
    expect(confidenceLabel(0.1)).toBe('low confidence')
    expect(confidenceLabel(null)).toBe('confidence unknown')
    expect(budgetLine(budget)).toBe('497 free searches left today')
    const m = 'gemini-flash-lite-latest'
    expect(costLine({ ...budget, free_tier_key: true }, 'quick', m, false)).toBe('Free (free-tier Gemini key).')
    expect(costLine(budget, 'quick', m, false)).toBe('About $0.0022 on your paid Gemini key.')
    expect(costLine({ ...budget, free_remaining: 0 }, 'quick', m, false)).toMatch(/used up/)
    expect(costLine({ ...budget, free_remaining: 0 }, 'quick', m, true)).toBe(
      'About $0.04 on your paid Gemini key (includes the search fee).',
    )
  })

  it('shows a host for a link', () => {
    expect(hostOf('https://example.org/a')).toBe('example.org')
    expect(hostOf('not a url')).toBe('not a url')
    expect(googleSearchUrl('白河 drama')).toBe('https://www.google.com/search?q=%E7%99%BD%E6%B2%B3%20drama')
  })
})
