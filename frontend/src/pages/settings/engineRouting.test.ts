import { describe, expect, it } from 'vitest'

import type { CapabilityRoute, EngineRouteStatus, EngineRouting } from '../../types/engineRouting'
import {
  choiceFromSelect,
  replaceCapability,
  replaceEngine,
  selectValue,
  statusBadge,
  tagsText,
  testBlockedReason,
  testedText,
  unsetBadge,
  unsetOptionLabel,
  withChoice,
  workingCount,
} from './engineRouting'

const cap = (over: Partial<CapabilityRoute> = {}): CapabilityRoute => ({
  id: 'llm.instructions',
  label: 'AI checks and line helpers',
  help: 'Improve, Why this?',
  requires: 'instructions',
  engine: 'gemini',
  default_engine: 'gemini',
  is_default: true,
  engine_supported: true,
  choices: ['claude', 'deepseek', 'gemini', 'test_offline'],
  ...over,
})

const eng = (over: Partial<EngineRouteStatus> = {}): EngineRouteStatus => ({
  engine: 'claude',
  tags: ['instructions', 'translate'],
  needs_key: true,
  key_configured: true,
  status: 'untested',
  last_test: null,
  ...over,
})

describe('engine routing view', () => {
  it('maps each status to a label and tone', () => {
    expect(statusBadge('working')).toEqual({ label: 'Working', tone: 'ok' })
    expect(statusBadge('failed')).toEqual({ label: 'Failed', tone: 'bad' })
    expect(statusBadge('untested')).toEqual({ label: 'Not tested', tone: 'neutral' })
    expect(statusBadge('not_configured')).toEqual({ label: 'No key', tone: 'warn' })
    expect(statusBadge('something_new').label).toBe('Not tested')
  })

  it('writes tags in plain words', () => {
    expect(tagsText(['translate', 'cheap', 'grounded_search'])).toBe('Translates · Low cost · Web search')
    expect(tagsText(['new_tag'])).toBe('new tag')
    expect(tagsText([])).toBe('')
  })

  it('says when an engine was tested', () => {
    const now = Date.parse('2026-09-29T12:00:00Z')
    expect(testedText('2026-09-29T11:59:30+00:00', now)).toBe('Tested just now')
    expect(testedText('2026-09-29T11:55:00+00:00', now)).toBe('Tested 5 min ago')
    expect(testedText('2026-09-29T09:00:00+00:00', now)).toBe('Tested 3 h ago')
    expect(testedText('2026-09-20T09:00:00+00:00', now)).toMatch(/^Tested \S/)
    expect(testedText('', now)).toBe('')
    expect(testedText('not a date', now)).toBe('')
  })

  it('blocks Test only when the key is missing', () => {
    expect(testBlockedReason(eng({ status: 'not_configured', key_configured: false }))).toMatch(/key/)
    expect(testBlockedReason(eng())).toBeNull()
    expect(testBlockedReason(eng({ test_blocked: 'NLLB downloads a large model' }))).toMatch(/NLLB/)
    expect(testBlockedReason(eng({ status: 'failed' }))).toBeNull()
  })

  it('maps the default option to null and back', () => {
    expect(selectValue(cap())).toBe('')
    expect(selectValue(cap({ engine: 'claude', is_default: false }))).toBe('claude')
    expect(choiceFromSelect('')).toBeNull()
    expect(choiceFromSelect('claude')).toBe('claude')
  })

  it('builds the optimistic entry for a choice', () => {
    expect(withChoice(cap(), 'claude')).toMatchObject({ engine: 'claude', is_default: false, engine_supported: true })
    expect(withChoice(cap({ engine: 'claude', is_default: false }), null)).toMatchObject({ engine: 'gemini', is_default: true })
    expect(withChoice(cap(), 'gemini').is_default).toBe(true)
    expect(withChoice(cap({ default_engine: 'deepl' }), null).engine_supported).toBe(false)
  })

  it('treats unset as "off" where the capability says so (Step 99)', () => {
    const off = cap({ unset_label: 'Off (no suggestions)' })
    // Picking the default engine is a real choice, not "off".
    expect(withChoice(off, 'gemini').is_default).toBe(false)
    expect(selectValue(withChoice(off, 'gemini'))).toBe('gemini')
    expect(withChoice(off, null).is_default).toBe(true)
    expect(unsetOptionLabel(off, (e) => e)).toBe('Off (no suggestions)')
    expect(unsetBadge(off)).toBe('off')
    expect(unsetOptionLabel(cap(), (e) => e.toUpperCase())).toBe('Use default (GEMINI)')
    expect(unsetBadge(cap())).toBe('default')
  })

  it('replaces one entry and counts working engines', () => {
    const r: EngineRouting = { capabilities: [cap(), cap({ id: 'other' })], engines: [eng(), eng({ engine: 'test_offline' })] }
    const r2 = replaceCapability(r, cap({ engine: 'claude', is_default: false }))
    expect(r2.capabilities.map((c) => c.engine)).toEqual(['claude', 'gemini'])
    const r3 = replaceEngine(r2, eng({ engine: 'test_offline', status: 'working' }))
    expect(r3.engines.map((e) => e.status)).toEqual(['untested', 'working'])
    expect(workingCount(r3)).toBe(1)
    expect(r.capabilities[0].engine).toBe('gemini') // not mutated
  })
})
