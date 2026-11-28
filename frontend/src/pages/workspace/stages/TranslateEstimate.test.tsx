import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { TranslateRunEstimate } from '../../../types/translateStage'
import { EstimateView } from './TranslateStage'

const estimate = (over: Partial<TranslateRunEstimate> = {}): TranslateRunEstimate => ({
  engine: 'claude', model: null, estimated_usd: 0.42, alignment_check_usd: null, target_line_count: 427, free: false,
  cap_applies: true, effective_cap_usd: null, monthly_refusal: false, estimate_above_cap: false,
  balance_too_low: false, ...over,
})
const text = (e: TranslateRunEstimate) => renderToStaticMarkup(<EstimateView e={e} />).replace(/<[^>]+>/g, '')

describe('the estimate before a Translate run', () => {
  it('warns, without blocking, when the DeepSeek balance looks too low', () => {
    expect(text(estimate({ engine: 'deepseek', balance_too_low: true }))).toContain('balance looks too low for this run. Top up at platform.deepseek.com')
    expect(text(estimate({ engine: 'deepseek' }))).not.toContain('balance looks too low')
  })

  it('adds the Source match check to the total and names it as its own part', () => {
    expect(text(estimate({ alignment_check_usd: 0.03 }))).toContain(': about $0.45 (translation $0.42, Source match check $0.03)')
    expect(text(estimate({ alignment_check_usd: 0.002 }))).toContain('Source match check under $0.01')
  })

  it('shows the translation alone when the check would not run', () => {
    const shown = text(estimate())
    expect(shown).toContain(': about $0.42')
    expect(shown).not.toContain('Source match')
  })
})
