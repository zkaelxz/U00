import { describe, expect, it } from 'vitest'

import type { ResearchBudget, ResearchField } from '../../types/research'
import {
  budgetLine, choicesFor, confidenceLabel, costLine, defaultChoices, effectiveChoices, hostOf,
} from './researchForm'

const row = (field: string, status: ResearchField['status']): ResearchField =>
  ({ field, value: 'v', status, sources: [], current: status === 'new' ? null : 'c' })

const budget: ResearchBudget = {
  free_daily_limit: 500, used_today: 3, free_remaining: 497, paid_price_per_search_usd: 0.035,
  free_tier_key: false, key_configured: true, monthly_cap_usd: 0, month_spend_usd: 0,
  models: ['gemini-flash-lite-latest'], modes: ['quick', 'deep', 'verify'],
}

describe('researchForm', () => {
  it('always offers keep/replace/save both for a conflict', () => {
    expect(choicesFor(row('title_en', 'conflict')).map((c) => c.value)).toEqual(['keep', 'replace', 'save_both'])
    expect(choicesFor(row('studio', 'new')).map((c) => c.value)).toEqual(['keep', 'replace'])
    expect(choicesFor(row('author', 'same'))).toEqual([])
  })

  it('never pre-chooses an overwrite', () => {
    const d = defaultChoices([row('title_en', 'conflict'), row('studio', 'new'), row('author', 'same')])
    expect(d).toEqual({ title_en: 'keep', studio: 'keep' })
    expect(effectiveChoices(d)).toEqual({})
    expect(effectiveChoices({ ...d, studio: 'replace' })).toEqual({ studio: 'replace' })
  })

  it('labels confidence, budget and cost', () => {
    expect(confidenceLabel(0.9)).toBe('high confidence')
    expect(confidenceLabel(0.6)).toBe('medium confidence')
    expect(confidenceLabel(0.1)).toBe('low confidence')
    expect(confidenceLabel(null)).toBe('confidence unknown')
    expect(budgetLine(budget)).toBe('497 of 500 free searches left today')
    expect(costLine({ ...budget, free_tier_key: true }, false)).toBe('Free (free-tier Gemini key).')
    expect(costLine({ ...budget, free_remaining: 0 }, false)).toMatch(/used up/)
    expect(costLine({ ...budget, free_remaining: 0 }, true)).toMatch(/\$0\.035 search fee/)
  })

  it('shows a host for a link', () => {
    expect(hostOf('https://example.org/a')).toBe('example.org')
    expect(hostOf('not a url')).toBe('not a url')
  })
})
