import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { ChatCard } from '../pages/assistant/ChatCard'
import TranslatePage from '../pages/Translate'
import { ChangelogCard } from '../pages/assistant/ChangelogCard'
import {
  RUN_KEY_PREFIX,
  RUN_VERSION,
  clearStoredRuns,
  loadStoredResult,
  readStored,
  removeStored,
  useStoredState,
  writeStored,
} from './useStoredRun'
import { useRunSelection } from '../pages/workspace/stages/useGlossaryRun'

const memory = (init: Record<string, string> = {}) => {
  const m = new Map(Object.entries(init))
  return {
    get length() {
      return m.size
    },
    key: (i: number) => [...m.keys()][i] ?? null,
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    keys: () => [...m.keys()],
  }
}
const boom = () => {
  throw new Error('blocked')
}
const isStr = (v: unknown): v is string => typeof v === 'string'
const flush = () => new Promise((r) => setTimeout(r, 0))
const notFound = () => new ApiError(404, { code: 'not_found', message: 'None.' })

afterEach(() => vi.unstubAllGlobals())

describe('stored values', () => {
  it('round-trips a value under the versioned key', () => {
    const s = memory()
    expect(writeStored(s, 'k', 'hello')).toBe(true)
    expect(JSON.parse(s.getItem(RUN_KEY_PREFIX + 'k')!)).toEqual({ v: RUN_VERSION, value: 'hello' })
    expect(readStored(s, 'k', isStr)).toBe('hello')
  })

  it('reads nothing for a missing, corrupt, other-version or wrong-typed copy', () => {
    const s = memory({
      [RUN_KEY_PREFIX + 'bad']: '{not json',
      [RUN_KEY_PREFIX + 'old']: JSON.stringify({ v: RUN_VERSION + 1, value: 'x' }),
      [RUN_KEY_PREFIX + 'num']: JSON.stringify({ v: RUN_VERSION, value: 5 }),
    })
    for (const k of ['missing', 'bad', 'old', 'num']) expect(readStored(s, k, isStr)).toBeNull()
  })

  it('does not store a value over the cap, and drops the older copy so it cannot come back', () => {
    const s = memory()
    writeStored(s, 'k', 'short')
    expect(writeStored(s, 'k', 'x'.repeat(100), 50)).toBe(false)
    expect(readStored(s, 'k', isStr)).toBeNull()
  })

  it('works without storage: nothing throws and nothing is read', () => {
    const blocked = { getItem: boom, setItem: boom, removeItem: boom }
    expect(writeStored(blocked, 'k', 'v')).toBe(false)
    expect(readStored(blocked, 'k', isStr)).toBeNull()
    expect(() => removeStored(blocked, 'k')).not.toThrow()
    expect(writeStored(null, 'k', 'v')).toBe(false)
    expect(readStored(null, 'k', isStr)).toBeNull()
  })

  it('sign-out forgets every stored result and stage draft, and only those', () => {
    const s = memory({
      [RUN_KEY_PREFIX + 'a']: '1',
      'baihe.draft.3.compare': '2',
      'baihe.pref.theme': '"dark"',
      other: '3',
    })
    clearStoredRuns(s as unknown as Storage)
    expect(s.keys().sort()).toEqual(['baihe.pref.theme', 'other'])
    expect(() => clearStoredRuns(null)).not.toThrow()
  })
})

describe('loadStoredResult (a panel mounting again)', () => {
  it('hands the result the server kept to every fresh mount', async () => {
    const server = { proposals: ['a', 'b'] }
    const fetchResult = vi.fn(async () => server)
    for (let mount = 0; mount < 2; mount++) {
      const onResult = vi.fn()
      loadStoredResult(7, { fetchResult, onResult })
      await flush()
      expect(onResult).toHaveBeenCalledWith(server)
    }
    expect(fetchResult).toHaveBeenCalledWith(7)
  })

  it('treats a 404 as no result yet, not an error', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    loadStoredResult(1, { fetchResult: async () => Promise.reject(notFound()), onResult, onError })
    await flush()
    expect(onResult).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports any other failure', async () => {
    const onError = vi.fn()
    const err = new ApiError(500, { code: 'x', message: 'down' })
    loadStoredResult(1, { fetchResult: async () => Promise.reject(err), onResult: vi.fn(), onError })
    await flush()
    expect(onError).toHaveBeenCalledWith(err)
  })

  it('shows nothing after the panel unmounted before the answer came', async () => {
    const onResult = vi.fn()
    const cancel = loadStoredResult(1, { fetchResult: async () => 'late', onResult })
    cancel()
    await flush()
    expect(onResult).not.toHaveBeenCalled()
  })
})

function Probe({ id }: { id: number }) {
  const [v] = useStoredState(`probe.${id}`, '', isStr)
  return <p>{`value:${v}`}</p>
}

describe('useStoredState (unmount, then mount again)', () => {
  it('shows what was kept, per key, and the initial value when nothing was', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    writeStored(s, 'probe.1', 'kept')
    expect(renderToStaticMarkup(<Probe id={1} />)).toContain('value:kept')
    expect(renderToStaticMarkup(<Probe id={2} />)).toContain('value:<')
  })

  it('still renders with storage blocked', () => {
    vi.stubGlobal('window', {
      get localStorage(): never {
        throw new Error('blocked')
      },
    })
    expect(renderToStaticMarkup(<Probe id={1} />)).toContain('value:<')
  })
})

describe('panels that keep their last result in the browser', () => {
  it('the changelog comes back with its form after leaving the Assistant page', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    const props = { engine: '', model: '', onModeOff: () => undefined }
    expect(renderToStaticMarkup(<ChangelogCard {...props} />)).not.toContain('changelog-result')
    writeStored(s, 'assistant.changelog', {
      from: 'v0.9',
      to: 'HEAD',
      result: { from_ref: 'v0.9', to_ref: 'HEAD', commit_count: 3, truncated: false, changelog: 'Fixed the Dub stage.' },
    })
    const html = renderToStaticMarkup(<ChangelogCard {...props} />)
    expect(html).toContain('changelog-result')
    expect(html).toContain('Fixed the Dub stage.')
    expect(html).toContain('value="v0.9"')
  })

  it('the chat comes back with its answers, and a question left waiting reads as cancelled', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    writeStored(s, 'assistant.chat', [
      { id: 1, question: 'Why is Dub slow?', response: { answer: 'Because of the GPU queue.', tier: 1, engine: 'ollama', local: true, model: '', tool_calls: [], proposed_patches: [], suggested_backlog: [] }, error: null },
      { id: 2, question: 'Still waiting?', response: null, error: null },
    ])
    const props = {
      settings: { engine_choices: [] } as never, engine: '', model: '', onEngine: () => undefined,
      onModel: () => undefined, onSettings: () => undefined, onModeOff: () => undefined, onAddToBacklog: async () => ({}) as never,
    }
    const html = renderToStaticMarkup(<ChatCard {...props} />)
    expect(html).toContain('Why is Dub slow?')
    expect(html).toContain('Because of the GPU queue.')
    expect(html).toContain('Cancelled. The assistant may still finish')
  })

  it('Quick translate keeps the text typed before leaving', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    writeStored(s, 'translate.text', '白河的故事')
    expect(renderToStaticMarkup(<TranslatePage />)).toContain('白河的故事')
  })
})

function SelectionProbe({ run, pick }: { run: string | null; pick?: string[] }) {
  const { picked, setPicked, edits } = useRunSelection(7, 'novel', run)
  if (pick && picked === null) setPicked(new Set(pick))
  return <p>{`picked:${picked ? [...picked].join('|') : 'default'} edits:${Object.keys(edits).join('|')}`}</p>
}

describe('glossary proposal selection (leave, then come back)', () => {
  it('restores the ticks and edits of the same run, and starts over for another run', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    expect(renderToStaticMarkup(<SelectionProbe run="r1" pick={['魏婴', '蓝湛']} />)).toContain('picked:魏婴|蓝湛')
    writeStored(s, 'glossary.7.novel.edits', { run: 'r1', value: { 魏婴: { translation: 'Wei Ying' } } })
    expect(renderToStaticMarkup(<SelectionProbe run="r1" />)).toContain('picked:魏婴|蓝湛 edits:魏婴')
    expect(renderToStaticMarkup(<SelectionProbe run="r2" />)).toContain('picked:default edits:<')
    expect(renderToStaticMarkup(<SelectionProbe run={null} />)).toContain('picked:default')
  })

  it('ignores a corrupt copy', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    writeStored(s, 'glossary.7.novel.picked', { run: 'r1', value: [1, 2] })
    writeStored(s, 'glossary.7.novel.edits', { run: 'r1', value: 'nope' })
    expect(renderToStaticMarkup(<SelectionProbe run="r1" />)).toContain('picked:default edits:<')
  })
})
