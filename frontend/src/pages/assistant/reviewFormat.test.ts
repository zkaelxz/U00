import { describe, expect, it } from 'vitest'
import { reviewEngineProblem, verdictInfo } from './reviewFormat'

describe('review verdicts', () => {
  it('maps known verdicts and treats unknown as no verdict', () => {
    expect(verdictInfo('concerns').label).toBe('Concerns')
    expect(verdictInfo('agrees').tone).toBe('ok')
    expect(verdictInfo('weird').label).toBe('No verdict')
  })
  it('asks for a review engine that differs from the answering engine', () => {
    expect(reviewEngineProblem('claude', '')).toMatch(/Pick a review engine/)
    expect(reviewEngineProblem('claude', 'claude')).toMatch(/different/)
    expect(reviewEngineProblem('claude', 'gemini')).toBeNull()
  })
})
