import { describe, expect, it } from 'vitest'

import type { TranslateEngine } from '../types/translate'
import {
  HISTORY_PREVIEW,
  engineOptionLabel,
  historyCount,
  historyLabel,
  historyTime,
  isDirection,
  orderEngines,
  pickEngine,
  pickLanguage,
  pickModel,
  swapDirection,
  visibleHistory,
} from './translatePage'

const eng = (name: string, key_configured = true, models: string[] | null = null): TranslateEngine => ({
  name,
  label: `${name} engine.`,
  free: false,
  models,
  key_configured,
})

describe('engine picker', () => {
  const all = [eng('claude', false, ['a', 'b']), eng('ollama'), eng('deepseek')]

  it('labels engines by name and marks the ones with no key', () => {
    expect(engineOptionLabel(eng('deepseek'))).toBe('DeepSeek')
    expect(engineOptionLabel(eng('claude', false))).toBe('Claude (no key)')
  })

  it('lists usable engines first, keeping API order', () => {
    expect(orderEngines(all).map((e) => e.name)).toEqual(['ollama', 'deepseek', 'claude'])
  })

  it('restores a remembered engine the server still lists, else the first usable one', () => {
    expect(pickEngine(all, 'deepseek')).toBe('deepseek')
    expect(pickEngine(all, 'claude')).toBe('claude')
    expect(pickEngine(all, 'gone')).toBe('ollama')
    expect(pickEngine(all, '')).toBe('ollama')
    // Settings' default wins over API order, but only while it can run.
    expect(pickEngine(all, '', 'deepseek')).toBe('deepseek')
    expect(pickEngine(all, '', 'claude')).toBe('ollama')
    expect(pickEngine(all, 'claude', 'deepseek')).toBe('claude')
    expect(pickEngine([eng('claude', false)], '')).toBe('')
    expect(pickEngine([], 'claude')).toBe('')
  })

  it('keeps a remembered model only if the engine offers it', () => {
    expect(pickModel(all[0], 'b')).toBe('b')
    expect(pickModel(all[0], 'z')).toBe('')
    expect(pickModel(all[1], 'b')).toBe('')
    expect(pickModel(undefined, 'b')).toBe('')
  })
})

describe('languages', () => {
  it('validates remembered direction and language', () => {
    expect(isDirection('to_english')).toBe(true)
    expect(isDirection('from_english')).toBe(true)
    expect(isDirection('sideways')).toBe(false)
    expect(pickLanguage('ko')).toBe('ko')
    expect(pickLanguage('en')).toBe('zh')
    expect(pickLanguage('')).toBe('zh')
  })

  it('swaps direction both ways', () => {
    expect(swapDirection('to_english')).toBe('from_english')
    expect(swapDirection('from_english')).toBe('to_english')
  })
})

describe('history', () => {
  it('humanizes languages and engine (no raw codes)', () => {
    const label = historyLabel({ source_language: 'zh', target_language: 'en', engine: 'ollama' })
    expect(label).toBe('Chinese → English · Ollama (local)')
    expect(historyLabel({ source_language: 'en', target_language: 'ja', engine: 'claude' })).toBe(
      'English → Japanese · Claude',
    )
    expect(historyLabel({ source_language: 'pt_br', target_language: '', engine: 'new_engine' })).not.toContain('_')
  })

  it('reads zone-less times as UTC and shows local time', () => {
    const utc = new Date(Date.UTC(2026, 8, 29, 18, 31)).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
    expect(historyTime('2026-09-29T18:31:05.123', 'en-GB')).toBe(utc)
    expect(historyTime('2026-09-29 18:31:05', 'en-GB')).toBe(utc)
    expect(historyTime('2026-09-29T18:31:05Z', 'en-GB')).toBe(utc)
    expect(historyTime('not a date')).toBe('not a date')
  })

  it('does not claim a total past the fetch limit', () => {
    expect(historyCount(3, 50)).toBe('3 saved, newest first')
    expect(historyCount(50, 50)).toBe('Last 50, newest first')
  })

  it('shows the first few until Show all', () => {
    const items = Array.from({ length: HISTORY_PREVIEW + 3 }, (_, i) => i)
    expect(visibleHistory(items, false)).toHaveLength(HISTORY_PREVIEW)
    expect(visibleHistory(items, true)).toHaveLength(items.length)
    expect(visibleHistory([1, 2], false)).toEqual([1, 2])
  })
})
