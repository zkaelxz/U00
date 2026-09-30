import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { AskResponse } from '../../types/assistant'
import {
  MODE_OFF_TEXT,
  NO_KEY_TEXT,
  PC_ONLY_TEXT,
  assistantErrorText,
  compactArgs,
  engineChanges,
  historyOf,
  isForbidden,
  kindLabel,
  plural,
  shortDate,
  type Exchange,
} from './assistantFormat'

const answer = (text: string): AskResponse => ({
  answer: text, proposed_patches: [], suggested_backlog: [], tool_calls: [], engine: 'claude', model: null,
})

describe('assistant page helpers', () => {
  it('builds chat_history from answered questions only', () => {
    const xs: Exchange[] = [
      { id: 1, question: 'Why?', response: answer('Because.'), error: null },
      { id: 2, question: 'Failed one', response: null, error: 'x' },
      { id: 3, question: 'And?', response: answer('Also.'), error: null },
      { id: 4, question: 'Pending', response: null, error: null },
    ]
    expect(historyOf(xs)).toEqual([
      { role: 'user', content: 'Why?' },
      { role: 'assistant', content: 'Because.' },
      { role: 'user', content: 'And?' },
      { role: 'assistant', content: 'Also.' },
    ])
  })

  it('turns API errors into plain text, never a path or key', () => {
    expect(assistantErrorText(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe(PC_ONLY_TEXT)
    expect(assistantErrorText(new ApiError(409, { code: 'conflict', message: 'Developer Mode is off.' }))).toBe(MODE_OFF_TEXT)
    expect(assistantErrorText(new ApiError(503, { code: 'dependency_unavailable', message: 'sk-abcdefghijk missing' }))).toBe(NO_KEY_TEXT)
    expect(assistantErrorText(new ApiError(422, { code: 'validation_error', message: 'Unknown git ref.' }))).toBe('Unknown git ref.')
    expect(assistantErrorText(new ApiError(422, { code: 'validation_error', message: 'bad /home/kae/repo/x' }))).toBe(
      'Some of the values entered are not valid. Check them and try again.',
    )
    expect(assistantErrorText(new ApiError(0, { code: 'network_error', message: 'x' }))).toBe('Could not reach the Baihe API. Is it running?')
    expect(assistantErrorText(new ApiError(500, { code: 'internal_error', message: 'C:\\Users\\kae\\key.txt' }))).not.toMatch(/Users/)
    expect(isForbidden(new ApiError(403, { code: 'forbidden', message: '' }))).toBe(true)
    expect(isForbidden(new ApiError(404, { code: 'not_found', message: '' }))).toBe(false)
    expect(isForbidden(null)).toBe(false)
  })

  it('shows tool arguments compactly', () => {
    expect(compactArgs({ path: 'services/a.py', lines: [1, 20] })).toBe('{"path":"services/a.py","lines":[1,20]}')
    expect(compactArgs(undefined)).toBe('{}')
    const long = compactArgs({ q: 'x'.repeat(500) }, 40)
    expect(long).toHaveLength(40)
    expect(long.endsWith('…')).toBe(true)
  })

  it('sends only the engine fields that changed; blank is the server default', () => {
    const saved = { engine: 'claude', model: null }
    expect(engineChanges(saved, 'claude', '')).toEqual({})
    expect(engineChanges(saved, 'gemini', '')).toEqual({ engine: 'gemini' })
    expect(engineChanges(saved, '', ' opus ')).toEqual({ engine: null, model: 'opus' })
    expect(engineChanges({ engine: null, model: 'm' }, '', 'm')).toEqual({})
  })

  it('labels kinds, dates and counts', () => {
    expect(kindLabel('bug')).toMatchObject({ label: 'Bug', tone: 'warn' })
    expect(kindLabel('feature').label).toBe('Feature')
    expect(shortDate('not a date')).toBe('not a date')
    expect(shortDate('2026-09-29T12:00:00')).toMatch(/2026/)
    expect(plural(1, 'tool')).toBe('1 tool')
    expect(plural(3, 'item')).toBe('3 items')
  })
})

describe('cloud consent (lead review)', () => {
  it('reads the engine from a consent 409 and leaves other 409s alone', async () => {
    const { ApiError } = await import('../../api/client')
    const { consentEngineOf, assistantErrorText, needsConsent, MODE_OFF_TEXT } = await import('./assistantFormat')
    const consent = new ApiError(409, { code: 'conflict', message: 'x', details: { reason: 'cloud_consent_required', engine: 'claude' } })
    expect(consentEngineOf(consent)).toBe('claude')
    expect(assistantErrorText(consent)).toMatch(/isn't allowed yet/)
    const off = new ApiError(409, { code: 'conflict', message: 'Developer Mode is off.' })
    expect(consentEngineOf(off)).toBeNull()
    expect(assistantErrorText(off)).toBe(MODE_OFF_TEXT)
    const s = { default_engine: 'ollama', local_engines: ['ollama'], cloud_consent: { claude: true, gemini: false } }
    expect(needsConsent(s, '')).toBe(false)
    expect(needsConsent(s, 'claude')).toBe(false)
    expect(needsConsent(s, 'gemini')).toBe(true)
  })
})
