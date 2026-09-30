import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  MAX_HISTORY_TURNS,
  addBacklogItem,
  askAssistant,
  clearBacklog,
  deleteBacklogItem,
  generateChangelog,
  getAssistantSettings,
  getAssistantTools,
  listBacklog,
  saveAssistantSettings,
  trimHistory,
} from './assistant'
import { ApiError } from './client'
import { getPcMode, resetPcModeForTests } from './pcOnly'
import type { ChatTurn } from '../types/assistant'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}
const sent = (mock: ReturnType<typeof vi.fn>, i = 0) => JSON.parse(mock.mock.calls[i][1].body)
const url = (mock: ReturnType<typeof vi.fn>, i = 0) => mock.mock.calls[i][0]
const header = (mock: ReturnType<typeof vi.fn>, name: string, i = 0) => new Headers(mock.mock.calls[i][1].headers).get(name)

afterEach(() => resetPcModeForTests())

const SETTINGS = { developer_mode: true, engine: null, model: null, engine_choices: ['claude', 'gemini'] }

describe('assistant api', () => {
  it('reads settings, tools and the backlog with plain GETs', async () => {
    const { mock, f } = reply(200, SETTINGS)
    await expect(getAssistantSettings(f)).resolves.toEqual(SETTINGS)
    await getAssistantTools(f)
    await listBacklog(f)
    expect([url(mock, 0), url(mock, 1), url(mock, 2)]).toEqual([
      '/api/assistant/settings',
      '/api/assistant/tools',
      '/api/assistant/backlog',
    ])
    expect(mock.mock.calls[0][1].method).toBeUndefined()
  })

  it('saves only the fields given, as a PC-only POST', async () => {
    const { mock, f } = reply(200, SETTINGS)
    await saveAssistantSettings({ developer_mode: true }, f)
    await saveAssistantSettings({ engine: null, model: 'opus' }, f)
    expect(url(mock)).toBe('/api/assistant/settings')
    expect(mock.mock.calls[0][1].method).toBe('POST')
    expect(sent(mock, 0)).toEqual({ developer_mode: true })
    expect(sent(mock, 1)).toEqual({ engine: null, model: 'opus' })
    expect(header(mock, 'X-Baihe-Local')).toBe('1')
  })

  it('asks with the question and history; engine and model only when picked', async () => {
    const { mock, f } = reply(200, { answer: 'a', proposed_patches: [], suggested_backlog: [], tool_calls: [], engine: 'claude', model: null })
    const history: ChatTurn[] = [{ role: 'user', content: 'q1' }, { role: 'assistant', content: 'a1' }]
    await askAssistant('Why?', history, '', '  ', f)
    await askAssistant('Why?', [], 'gemini', 'flash', f)
    expect(url(mock)).toBe('/api/assistant/ask')
    expect(sent(mock, 0)).toEqual({ question: 'Why?', chat_history: history })
    expect(sent(mock, 1)).toEqual({ question: 'Why?', chat_history: [], engine: 'gemini', model: 'flash' })
  })

  it('sends at most the last 20 turns of history', async () => {
    const long: ChatTurn[] = Array.from({ length: 25 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `t${i}` }))
    expect(trimHistory(long)).toHaveLength(MAX_HISTORY_TURNS)
    expect(trimHistory(long)[0].content).toBe('t5')
    const { mock, f } = reply(200, { answer: 'a', proposed_patches: [], suggested_backlog: [], tool_calls: [], engine: 'claude', model: null })
    await askAssistant('q', long, null, null, f)
    expect(sent(mock).chat_history).toHaveLength(20)
  })

  it('builds a changelog request; to_ref only when set', async () => {
    const { mock, f } = reply(200, { changelog: '- x', commit_count: 1, from_ref: 'v1', to_ref: 'HEAD' })
    await generateChangelog(' v1 ', '', null, null, f)
    await generateChangelog('v1', 'v2', 'claude', '', f)
    expect(url(mock)).toBe('/api/assistant/changelog')
    expect(sent(mock, 0)).toEqual({ from_ref: 'v1' })
    expect(sent(mock, 1)).toEqual({ from_ref: 'v1', to_ref: 'v2', engine: 'claude' })
  })

  it('adds, deletes and clears backlog items with confirm', async () => {
    const { mock, f } = reply(200, { id: 3, kind: 'bug', text: 't', created_at: 'x' })
    await addBacklogItem('bug', 't', f)
    await deleteBacklogItem(3, f)
    await clearBacklog(f)
    expect(url(mock, 0)).toBe('/api/assistant/backlog')
    expect(sent(mock, 0)).toEqual({ kind: 'bug', text: 't' })
    expect(url(mock, 1)).toBe('/api/assistant/backlog/3/delete')
    expect(sent(mock, 1)).toEqual({ confirm: true })
    expect(url(mock, 2)).toBe('/api/assistant/backlog/clear')
    expect(sent(mock, 2)).toEqual({ confirm: true })
  })

  it('surfaces 409 and 503 as ApiError; a 403 on a write marks the tab remote', async () => {
    const off = reply(409, { error: { code: 'conflict', message: 'Developer Mode is off.' } })
    await expect(askAssistant('q', [], null, null, off.f)).rejects.toMatchObject({ status: 409, code: 'conflict' })
    const nokey = reply(503, { error: { code: 'dependency_unavailable', message: 'No key.' } })
    await expect(generateChangelog('v1', undefined, null, null, nokey.f)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('unknown')
    const remote = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(clearBacklog(remote.f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
