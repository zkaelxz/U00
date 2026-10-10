import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  changedKeys,
  clearDraft,
  DRAFT_VERSION,
  draftKey,
  pickDraft,
  readDraft,
  useStageDraft,
  writeDraft,
} from './useStageDraft'

const memory = (init: Record<string, string> = {}) => {
  const m = new Map(Object.entries(init))
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    keys: () => [...m.keys()],
  }
}
const boom = () => {
  throw new Error('blocked')
}
const SHAPE = { language: '', count: 0, on: false, list: [] as string[] }

describe('stage draft store', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('round-trips a form under one versioned key per drama and stage', () => {
    const s = memory()
    expect(readDraft(s, 1, 'transcribe')).toBeNull()
    expect(writeDraft(s, 1, 'transcribe', { language: 'ja', on: true })).toBe(true)
    expect(s.keys()).toEqual([draftKey(1, 'transcribe')])
    expect(draftKey(1, 'transcribe')).toBe('baihe.draft.1.transcribe')
    expect(JSON.parse(s.getItem(draftKey(1, 'transcribe'))!)).toEqual({ v: DRAFT_VERSION, values: { language: 'ja', on: true } })
    expect(readDraft(s, 1, 'transcribe')).toEqual({ language: 'ja', on: true })
    expect(clearDraft(s, 1, 'transcribe')).toBe(true)
    expect(readDraft(s, 1, 'transcribe')).toBeNull()
  })

  it('keeps dramas and stages apart', () => {
    const s = memory()
    writeDraft(s, 1, 'transcribe', { language: 'ja' })
    writeDraft(s, 1, 'translate', { engine: 'claude' })
    writeDraft(s, 2, 'transcribe', { language: 'ko' })
    expect(readDraft(s, 1, 'transcribe')).toEqual({ language: 'ja' })
    expect(readDraft(s, 1, 'translate')).toEqual({ engine: 'claude' })
    expect(readDraft(s, 2, 'transcribe')).toEqual({ language: 'ko' })
    expect(readDraft(s, 3, 'transcribe')).toBeNull()
    clearDraft(s, 1, 'transcribe')
    expect(readDraft(s, 2, 'transcribe')).toEqual({ language: 'ko' })
  })

  it('treats blocked or missing storage as no draft and never throws', () => {
    const blocked = { getItem: boom, setItem: boom, removeItem: boom }
    expect(readDraft(blocked, 1, 'dub')).toBeNull()
    expect(writeDraft(blocked, 1, 'dub', { a: 1 })).toBe(false)
    expect(clearDraft(blocked, 1, 'dub')).toBe(false)
    expect(readDraft(null, 1, 'dub')).toBeNull()
    expect(writeDraft(null, 1, 'dub', { a: 1 })).toBe(false)
  })

  it('drops a draft from another version or a corrupt one', () => {
    const key = draftKey(1, 'export')
    expect(readDraft(memory({ [key]: JSON.stringify({ v: DRAFT_VERSION + 1, values: { fmt: 'vtt' } }) }), 1, 'export')).toBeNull()
    expect(readDraft(memory({ [key]: JSON.stringify({ v: DRAFT_VERSION - 1, values: { fmt: 'vtt' } }) }), 1, 'export')).toBeNull()
    expect(readDraft(memory({ [key]: JSON.stringify({ fmt: 'vtt' }) }), 1, 'export')).toBeNull()
    expect(readDraft(memory({ [key]: JSON.stringify({ v: DRAFT_VERSION, values: ['x'] }) }), 1, 'export')).toBeNull()
    expect(readDraft(memory({ [key]: '{nope' }), 1, 'export')).toBeNull()
  })

  it('picks only the keys of the shape that hold the same type', () => {
    expect(pickDraft(null, SHAPE)).toEqual({})
    expect(pickDraft({ language: 'ja', count: '3', on: true, list: 'a', extra: 1 }, SHAPE)).toEqual({ language: 'ja', on: true })
    expect(pickDraft({ count: 3, list: ['a'], language: null }, SHAPE)).toEqual({ count: 3, list: ['a'] })
    expect(pickDraft({ list: { 0: 'a' } }, SHAPE)).toEqual({})
  })

  it('names the keys a form changed from its saved base', () => {
    const base = { beam: '5', fast: false, size: 'medium' }
    expect(changedKeys({ ...base }, base)).toEqual({})
    expect(changedKeys({ beam: '8', fast: false, size: 'medium' }, base)).toEqual({ beam: '8' })
  })

  it('the hook reads the draft once and saves and clears under the same key', () => {
    const s = memory()
    writeDraft(s, 7, 'dub', { language: 'ko', count: 'bad' })
    vi.stubGlobal('window', { localStorage: s })
    let api: ReturnType<typeof useStageDraft<typeof SHAPE>> | null = null
    const Probe = () => {
      api = useStageDraft(7, 'dub', SHAPE)
      return createElement('span', null, api.draft.language ?? 'none')
    }
    expect(renderToStaticMarkup(createElement(Probe))).toBe('<span>ko</span>')
    expect(api!.draft).toEqual({ language: 'ko' })
    expect(api!.raw).toEqual({ language: 'ko', count: 'bad' })
    api!.save({ language: 'zh', count: 2 })
    expect(readDraft(s, 7, 'dub')).toEqual({ language: 'zh', count: 2 })
    api!.clear()
    expect(readDraft(s, 7, 'dub')).toBeNull()
    expect(renderToStaticMarkup(createElement(Probe))).toBe('<span>none</span>')
  })

  it('the hook still renders when storage is blocked', () => {
    vi.stubGlobal('window', { localStorage: { getItem: boom, setItem: boom, removeItem: boom } })
    const Probe = () => {
      const { draft, save, clear } = useStageDraft(1, 'dub', SHAPE)
      save({ language: 'ja' })
      clear()
      return createElement('span', null, draft.language ?? 'none')
    }
    expect(renderToStaticMarkup(createElement(Probe))).toBe('<span>none</span>')
  })
})
