import { describe, expect, it } from 'vitest'

import type { SetupReadiness } from '../../types/setup'
import type { CapabilityRoute, EngineRouteStatus, EngineRouting } from '../../types/engineRouting'
import {
  choiceFromSelect,
  engineRows,
  keysSetCount,
  replaceCapability,
  replaceEngine,
  selectValue,
  statusBadge,
  tagsText,
  taskEngineNeed,
  taskNeedsOnce,
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
  choices: ['claude', 'deepseek', 'gemini', 'ollama'],
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
    expect(testBlockedReason(eng({ test_blocked: 'Downloads a large model first' }))).toMatch(/large model/)
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
    expect(withChoice(cap({ default_engine: 'fake_mt' }), null).engine_supported).toBe(false)
  })

  it('treats unset as "off" where the capability says so (Step 99)', () => {
    const off = cap({ unset_label: 'Off (no suggestions)' })
    // Picking the default engine is a real choice, not "off".
    expect(withChoice(off, 'gemini').is_default).toBe(false)
    expect(selectValue(withChoice(off, 'gemini'))).toBe('gemini')
    expect(withChoice(off, null).is_default).toBe(true)
    expect(unsetOptionLabel(off, (e) => e)).toBe('Off (no suggestions)')
    expect(unsetBadge(off)).toBe('Off')
    expect(unsetOptionLabel(cap(), (e) => e.toUpperCase())).toBe('Use default (GEMINI)')
    expect(unsetBadge(cap())).toBe('Default')
  })

  it('replaces one entry and counts working engines', () => {
    const r: EngineRouting = { capabilities: [cap(), cap({ id: 'other' })], engines: [eng(), eng({ engine: 'ollama' })] }
    const r2 = replaceCapability(r, cap({ engine: 'claude', is_default: false }))
    expect(r2.capabilities.map((c) => c.engine)).toEqual(['claude', 'gemini'])
    const r3 = replaceEngine(r2, eng({ engine: 'ollama', status: 'working' }))
    expect(r3.engines.map((e) => e.status)).toEqual(['untested', 'working'])
    expect(workingCount(r3)).toBe(1)
    expect(r.capabilities[0].engine).toBe('gemini') // not mutated
  })
})

describe('merged engines and keys list', () => {
  const routing: EngineRouting = {
    capabilities: [],
    engines: [
      eng({ engine: 'claude' }),
      eng({ engine: 'ollama', tags: ['local', 'translate'], needs_key: false }),
      eng({ engine: 'fake', tags: [], needs_key: false }),
    ],
  }
  const keys = { claude: false, groq: true, hf_token: false, ollama_url: true }

  it('lists routed engines first, then keys no engine uses, never the server addresses', () => {
    const rows = engineRows(routing, keys)
    expect(rows.map((r) => r.engine)).toEqual(['claude', 'ollama', 'fake', 'groq', 'hf_token'])
    expect(rows.map((r) => r.keyState)).toEqual(['missing', 'none', 'none', 'set', 'missing'])
    expect(rows.map((r) => r.writable)).toEqual([true, false, false, true, true])
    expect(rows.map((r) => r.cost)).toEqual(['Paid', 'Free · runs on this PC', 'Free', 'Paid', 'Free'])
    expect(rows[3].status).toBeNull()
  })

  it('takes key state from the settings overview so a saved key shows at once', () => {
    expect(engineRows(routing, { ...keys, claude: true })[0].keyState).toBe('set')
  })

  it('still lists the keys while routing is loading or failed', () => {
    expect(engineRows(null, keys).map((r) => r.engine)).toEqual(['claude', 'groq', 'hf_token'])
  })

  it('counts only engines that take a key', () => {
    expect(keysSetCount(engineRows(routing, keys))).toEqual({ set: 1, total: 3 })
  })
})

describe('taskEngineNeed: why a task cannot run before it is run', () => {
  const rowFor = (engine: string, keyState: 'set' | 'missing' | 'none') => ({
    engine, label: engine === 'claude' ? 'Claude' : engine === 'ollama' ? 'Ollama' : engine, status: null, keyState, writable: true, cost: '',
  })
  const ready = { anthropic: true, openai: true, ollama_reachable: true } as unknown as SetupReadiness

  it('says nothing for an unknown readiness, an unknown engine or a working engine', () => {
    expect(taskEngineNeed(cap({ engine: 'claude', is_default: false }), [rowFor('claude', 'set')], null)).toBeNull()
    expect(taskEngineNeed(cap({ engine: 'claude', is_default: false }), [], ready)).toBeNull()
    expect(taskEngineNeed(cap({ engine: 'claude', is_default: false }), [rowFor('claude', 'set')], ready)).toBeNull()
  })

  it('names the missing key, pointing at the list on the same page', () => {
    const n = taskEngineNeed(cap({ engine: 'claude', is_default: false }), [rowFor('claude', 'missing')], ready)!
    expect(n.id).toBe('task_key')
    expect(n.reason).toContain('Add one under Engines and keys below')
  })

  it('puts a missing package before a missing key', () => {
    const n = taskEngineNeed(cap({ engine: 'claude', is_default: false }), [rowFor('claude', 'missing')], { ...ready, anthropic: false })!
    expect(n.id).toBe('task_package')
    expect(n.packages).toEqual(['anthropic'])
  })

  it('checks Ollama through the reachability answer, never a key', () => {
    const n = taskEngineNeed(cap({ engine: 'ollama', is_default: false }), [rowFor('ollama', 'none')], { ...ready, ollama_reachable: false })!
    expect(n.id).toBe('task_ollama')
    expect(n.action).toBe('recheck')
    expect(taskEngineNeed(cap({ engine: 'ollama', is_default: false }), [rowFor('ollama', 'none')], ready)).toBeNull()
  })

  it('skips a task that is switched off', () => {
    const off = cap({ engine: 'claude', is_default: true, unset_label: 'Off' })
    expect(taskEngineNeed(off, [rowFor('claude', 'missing')], ready)).toBeNull()
  })
})

describe('taskNeedsOnce', () => {
  it('shows one note per reason, on the first task that needs it', () => {
    const caps = [
      cap({ id: 'a', engine: 'claude', is_default: false }),
      cap({ id: 'b', engine: 'claude', is_default: false }),
      cap({ id: 'c', engine: 'gemini', is_default: false }),
    ]
    const row = (engine: string, label: string) => ({ engine, label, status: null, keyState: 'missing' as const, writable: true, cost: '' })
    const needs = taskNeedsOnce(caps, [row('claude', 'Claude'), row('gemini', 'Gemini')], null)
    expect([...needs.keys()]).toEqual(['a', 'c'])
    expect(needs.get('a')?.reason).toMatch(/^Claude has no API key yet/)
  })
})
