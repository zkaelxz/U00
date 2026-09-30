import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { AskResponse } from '../../types/assistant'
import type { Exchange } from './assistantFormat'
import { escalationPlan, failureText, nextTierOf, privacyNote, reportRequest, sendSummary, tierFailureOf, tierLabel } from './escalation'

const answer = (text: string, over: Partial<AskResponse> = {}): AskResponse => ({
  answer: text,
  proposed_patches: [],
  suggested_backlog: [],
  tool_calls: [],
  engine: 'ollama',
  model: null,
  tier: 1,
  local: true,
  next_engine: 'gemini',
  evidence: '',
  ...over,
})

const ex = (id: number, question: string, response: AskResponse | null, error: string | null = null): Exchange => ({ id, question, response, error })

describe('assistant tier ladder helpers', () => {
  it('labels an answer with its tier and engine', () => {
    expect(tierLabel(1, 'ollama', true)).toBe('Tier 1 · Ollama · on this PC')
    expect(tierLabel(2, 'gemini', false)).toBe('Tier 2 · Gemini · cloud')
    expect(tierLabel(null, 'claude', false)).toBe('Claude · cloud')
  })

  it('offers the next tier after an answer or a failure, never past the last', () => {
    expect(nextTierOf(ex(1, 'q', answer('a')))).toBe('gemini')
    expect(nextTierOf(ex(1, 'q', answer('a', { next_engine: null })))).toBeNull()
    const failed: Exchange = { ...ex(2, 'q', null, 'x'), failure: { reason: 'unreachable', engine: 'ollama', tier: 1, nextEngine: 'gemini' } }
    expect(nextTierOf(failed)).toBe('gemini')
    expect(nextTierOf(ex(3, 'q', null, 'Developer Mode is off.'))).toBeNull()
  })

  it('reads a tier failure from the API error and says it plainly', () => {
    const e = new ApiError(500, {
      code: 'application_error',
      message: 'The engine call failed: /home/me/x.py',
      details: { reason: 'rate_limited', engine: 'gemini', tier: 2, next_engine: 'claude' },
    })
    const f = tierFailureOf(e)
    expect(f).toEqual({ reason: 'rate_limited', engine: 'gemini', tier: 2, nextEngine: 'claude' })
    expect(failureText(f!)).toContain('rate limit')
    expect(failureText(f!)).not.toContain('/home')
    expect(failureText({ reason: 'unreachable', engine: 'ollama', tier: 1, nextEngine: null })).toContain('Ollama is running')
    expect(tierFailureOf(new ApiError(409, { code: 'conflict', message: 'x', details: { reason: 'cloud_consent_required', engine: 'claude' } }))).toBeNull()
    expect(tierFailureOf(null)).toBeNull()
  })

  it('plans an escalation from the chat before the question and its evidence, bounded', () => {
    const many: Exchange[] = Array.from({ length: 14 }, (_, i) => ex(i + 1, `q${i}`, answer(`a${i}`)))
    const last = ex(99, 'why?', answer('not sure', { evidence: 'RESULT t1 (inspect_logs, ok):\nERROR' }))
    const plan = escalationPlan([...many, last], many.length)
    expect(plan.question).toBe('why?')
    expect(plan.evidence).toContain('RESULT t1')
    expect(plan.history).toHaveLength(20) // the API's limit: the newest 20 turns
    expect(plan.history[plan.history.length - 1]).toEqual({ role: 'assistant', content: 'a13' })
    expect(plan.history.some((t) => t.content === 'not sure')).toBe(false) // the answer being replaced isn't sent
  })

  it('lists exactly what will be sent, and says so for a first question with no tool output', () => {
    const lines = sendSummary({ question: 'q', history: [], evidence: '' })
    expect(lines).toEqual(['Your question.', 'No earlier messages (this is the first question).', 'No tool output (the earlier tiers gathered none).'])
    const more = sendSummary({ question: 'q', history: [{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }], evidence: 'x'.repeat(1200) })
    expect(more[1]).toBe('The last 2 messages of this chat.')
    // The evidence can hold every earlier tier's tool output, not just the last tier's.
    expect(more[2]).toBe('What the earlier tiers’ read-only tools found (1,200 characters, redacted).')
  })

  it('warns that a cloud tier leaves the PC, and about the Gemini free tier', () => {
    expect(privacyNote('ollama', true)).toContain('nothing leaves it')
    expect(privacyNote('claude', false)).toContain('leaves your PC')
    expect(privacyNote('claude', false)).not.toContain('free tier')
    expect(privacyNote('gemini', false)).toContain('Google may use what you send to improve its products')
  })

  it('builds the developer report request from the chat, an unanswered question and the latest evidence', () => {
    const xs = [ex(1, 'q1', answer('a1', { evidence: 'E1' })), ex(2, 'q2', null, 'Ollama didn’t answer.')]
    expect(reportRequest(xs)).toEqual({
      chat_history: [
        { role: 'user', content: 'q1' },
        { role: 'assistant', content: 'a1' },
      ],
      question: 'q2',
      evidence: 'E1',
    })
    expect(reportRequest([ex(1, 'q1', answer('a1'))]).question).toBeUndefined()
  })
})
