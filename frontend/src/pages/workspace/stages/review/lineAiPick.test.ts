import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TranslateEngine } from '../../../../types/translate'
import {
  engineBlockReason, loadPick, modelBlockReason, NEEDS_PAID_REASON, NO_KEY_REASON, NO_PICK, savePick, usablePick,
} from './lineAiPick'

const eng = (over: Partial<TranslateEngine> = {}): TranslateEngine => ({
  name: 'claude', label: 'Paid, cloud.', free: false, models: ['m1', 'm2'], key_configured: true, ...over,
})
const claude = eng()
const ollama = eng({ name: 'ollama', free: true, models: ['gemma', 'cloud-x'], cloud_models: ['cloud-x'] })

function stubStorage(store: Record<string, string> = {}) {
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
  })
  return store
}

afterEach(() => vi.unstubAllGlobals())

describe('engineBlockReason', () => {
  it('blocks an engine without a key, and a paid one without the permission', () => {
    expect(engineBlockReason(eng({ key_configured: false }), true)).toBe(NO_KEY_REASON)
    expect(engineBlockReason(claude, false)).toBe(NEEDS_PAID_REASON)
    expect(engineBlockReason(claude, true)).toBeNull()
  })
  it('lets a free engine through without the paid permission, for every line AI tool', () => {
    expect(engineBlockReason(ollama, false)).toBeNull()
  })
})

describe('modelBlockReason', () => {
  it('holds an Ollama cloud tag to the paid permission', () => {
    expect(modelBlockReason(ollama, 'cloud-x', false)).toBe(NEEDS_PAID_REASON)
    expect(modelBlockReason(ollama, 'cloud-x', true)).toBeNull()
    expect(modelBlockReason(ollama, 'gemma', false)).toBeNull()
  })
})

describe('usablePick', () => {
  const engines = [claude, ollama]
  it('keeps a pick that still works', () => {
    expect(usablePick({ engine: 'claude', model: 'm2' }, engines, true)).toEqual({ engine: 'claude', model: 'm2' })
  })
  it('falls back to the default for an unknown, keyless or forbidden engine', () => {
    expect(usablePick({ engine: 'gone', model: '' }, engines, true)).toEqual(NO_PICK)
    expect(usablePick({ engine: 'claude', model: '' }, [eng({ key_configured: false })], true)).toEqual(NO_PICK)
    expect(usablePick({ engine: 'claude', model: '' }, engines, false)).toEqual(NO_PICK)
  })
  it('keeps a free engine pick for a member without the paid permission', () => {
    expect(usablePick({ engine: 'ollama', model: '' }, engines, false)).toEqual({ engine: 'ollama', model: '' })
  })
  it('drops only the model when the engine stays valid but the model does not', () => {
    expect(usablePick({ engine: 'claude', model: 'old' }, engines, true)).toEqual({ engine: 'claude', model: '' })
    expect(usablePick({ engine: 'ollama', model: 'cloud-x' }, engines, false)).toEqual({ engine: 'ollama', model: '' })
  })
  it('keeps a typed model for an engine without a model list', () => {
    const free = eng({ name: 'deepseek', models: null })
    expect(usablePick({ engine: 'deepseek', model: 'deepseek-chat' }, [free], true).model).toBe('deepseek-chat')
  })
  it('uses the default when the engine list is empty', () => {
    expect(usablePick({ engine: 'claude', model: '' }, [], true)).toEqual(NO_PICK)
  })
})

describe('remembered pick', () => {
  it('round-trips through localStorage and clears on the default', () => {
    const store = stubStorage()
    savePick({ engine: 'claude', model: 'm1' })
    expect(loadPick()).toEqual({ engine: 'claude', model: 'm1' })
    savePick(NO_PICK)
    expect(Object.keys(store)).toEqual([])
    expect(loadPick()).toEqual(NO_PICK)
  })
  it('ignores a corrupt value', () => {
    stubStorage({ 'baihe.review.lineAiPick': '{"engine":5}' })
    expect(loadPick()).toEqual(NO_PICK)
    stubStorage({ 'baihe.review.lineAiPick': 'not json' })
    expect(loadPick()).toEqual(NO_PICK)
  })
  it('survives storage that throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked') },
      setItem: () => { throw new Error('blocked') },
      removeItem: () => { throw new Error('blocked') },
    })
    expect(loadPick()).toEqual(NO_PICK)
    expect(() => savePick({ engine: 'claude', model: '' })).not.toThrow()
  })
})
