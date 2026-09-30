import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { ModelStatusItem } from '../../api/models'
import { parseCompareParam } from '../benchmark/benchmarkForm'
import { parseRoute } from '../../router'
import {
  compareHref, engineCheckLines, formatCheckedAt, healthBadge, kindHelp, lastCheckedLine, modelHealthError,
  modelStatusLabel, modelStatusTone, sortModelItems, splitModelItems, whereLabel,
} from './modelHealth'

const SEV: Record<string, number> = { retired: 3, not_listed: 3, deprecated: 2, legacy: 1, current: 0, unknown: 0 }

const item = (over: Partial<ModelStatusItem> = {}): ModelStatusItem => {
  const status = over.status ?? 'current'
  return {
    engine: 'claude', model: 'claude-sonnet-5', kind: 'default', where: 'claude built-in default', status,
    message: 'x', replacement: null, note: null, listed_by_provider: null, severity: SEV[status], can_switch: false, ...over,
  }
}

describe('status labels and tones', () => {
  it('maps each status to plain words and a tone', () => {
    expect(['retired', 'not_listed', 'deprecated', 'legacy', 'current', 'unknown'].map((s) => [modelStatusLabel(s), modelStatusTone(s)])).toEqual([
      ['Retired', 'bad'],
      ['No longer listed', 'bad'],
      ['Deprecated', 'warn'],
      ['Older model', 'info'],
      ['Current', 'ok'],
      ['Not checked', 'neutral'],
    ])
    expect(modelStatusLabel('brand_new')).toBe('Brand new')
    expect(modelStatusTone('brand_new')).toBe('neutral')
  })
})

describe('ordering', () => {
  const rows = [
    item({ where: 'claude built-in default' }),
    item({ status: 'legacy', kind: 'tier', where: 'Workflow tier: Balanced' }),
    item({ status: 'retired', kind: 'default', where: 'deepseek built-in default', engine: 'deepseek' }),
    item({ status: 'retired', kind: 'preset', where: 'Preset: B', preset_id: 2 }),
    item({ status: 'deprecated', kind: 'preset', where: 'Preset: A', preset_id: 1 }),
    item({ status: 'unknown', kind: 'preset', where: 'Preset: C', preset_id: 3 }),
    item({ status: 'not_listed', kind: 'preset', where: 'Preset: A2', preset_id: 4 }),
  ]

  it('sorts most severe first, then presets, tiers, defaults, then by where', () => {
    expect(sortModelItems(rows).map((r) => r.where)).toEqual([
      'Preset: A2', 'Preset: B', 'deepseek built-in default', 'Preset: A', 'Workflow tier: Balanced', 'Preset: C', 'claude built-in default',
    ])
  })

  it('splits rows needing attention (severity 1+) from current / not checked ones', () => {
    const { attention, others } = splitModelItems(rows)
    expect(attention.map((r) => r.status)).toEqual(['not_listed', 'retired', 'retired', 'deprecated', 'legacy'])
    expect(others.map((r) => r.status)).toEqual(['unknown', 'current'])
  })
})

describe('header badge', () => {
  it('counts warnings; bad when anything is retired or gone, warn when only deprecated', () => {
    expect(healthBadge({ warnings: 2, items: [item({ status: 'retired' }), item({ status: 'deprecated' })] })).toEqual({ text: '2 warnings', tone: 'bad' })
    expect(healthBadge({ warnings: 1, items: [item({ status: 'deprecated' })] })).toEqual({ text: '1 warning', tone: 'warn' })
    expect(healthBadge({ warnings: 0, items: [item({ status: 'legacy' }), item({ status: 'legacy' })] })).toEqual({ text: '2 older models', tone: 'info' })
    expect(healthBadge({ warnings: 0, items: [item(), item({ status: 'unknown' })] })).toEqual({ text: 'No warnings', tone: 'ok' })
  })
})

describe('row text', () => {
  it('names where the model is set in plain words', () => {
    expect(whereLabel(item({ engine: 'deepseek', kind: 'default', where: 'deepseek built-in default' }))).toBe('DeepSeek: built-in default')
    expect(whereLabel(item({ kind: 'preset', where: 'Preset: Drama A' }))).toBe('Preset: Drama A')
    expect(whereLabel(item({ kind: 'tier', where: 'Workflow tier: Fast' }))).toBe('Workflow tier: Fast')
  })

  it('built-in defaults and tiers say to update the app; presets without a switch say where to change them', () => {
    expect(kindHelp(item({ kind: 'default' }))).toBe('Built into the app — update the app to change it.')
    expect(kindHelp(item({ kind: 'tier' }))).toBe('Built into the app — update the app to change it.')
    expect(kindHelp(item({ kind: 'preset', can_switch: true, replacement: 'b' }))).toBeNull()
    expect(kindHelp(item({ kind: 'preset', can_switch: false, replacement: 'new-model' }))).toMatch(/^new-model isn't offered for this engine/)
    expect(kindHelp(item({ kind: 'preset', can_switch: false }))).toBe("To pick a different model, change the preset in a drama's Translate step.")
  })
})

describe('compare link', () => {
  it('links the model and its replacement for the Benchmark Lab to read back', () => {
    const href = compareHref(item({ engine: 'claude', model: 'claude-sonnet-4-6', replacement: 'claude-sonnet-5' }))
    expect(href).toBe('#/benchmark?compare=claude:claude-sonnet-4-6,claude:claude-sonnet-5')
    const route = parseRoute(href!)
    expect(route).toEqual({ name: 'benchmark', compare: 'claude:claude-sonnet-4-6,claude:claude-sonnet-5' })
  })

  it('keeps odd model names intact through the round trip', () => {
    const href = compareHref(item({ engine: 'ollama', model: 'qwen2.5:14b', replacement: 'qwen3:8b' }))!
    const route = parseRoute(href)
    expect(route.name === 'benchmark' && parseCompareParam(route.compare)).toEqual([
      { engine: 'ollama', model: 'qwen2.5:14b' }, { engine: 'ollama', model: 'qwen3:8b' },
    ])
  })

  it('has no link without a (different) replacement', () => {
    expect(compareHref(item({ replacement: null }))).toBeNull()
    expect(compareHref(item({ model: 'a', replacement: 'a' }))).toBeNull()
    for (const status of ['retired', 'not_listed', 'not_offered'] as const) {
      expect(compareHref(item({ status, model: 'old', replacement: 'new' }))).toBeNull()
    }
  })
})

describe('last check', () => {
  it('shows the stored UTC time, or that no check ran yet', () => {
    expect(formatCheckedAt('2026-09-30T14:03:11.123456')).toBe('2026-09-30 14:03 UTC')
    expect(formatCheckedAt('garbage')).toBeNull()
    expect(lastCheckedLine(null)).toBe('Providers not checked yet')
    expect(lastCheckedLine('2026-09-30T14:03:11')).toBe('Providers last checked 2026-09-30 14:03 UTC')
  })

  it('lists failed engines first with a plain reason; key- or path-like text is dropped', () => {
    const lines = engineCheckLines({
      claude: { ok: true, model_count: 12 },
      gemini: { ok: false, model_count: 0, error: 'HTTPError: 403 Forbidden' },
      deepseek: { ok: false, model_count: 0, error: 'Bearer abc123 was refused' },
    })
    expect(lines.map((l) => [l.label, l.ok, l.text])).toEqual([
      ['DeepSeek', false, "Couldn't check: the provider did not answer."],
      ['Gemini', false, "Couldn't check: HTTPError: 403 Forbidden"],
      ['Claude', true, '12 models listed'],
    ])
    expect(engineCheckLines({ claude: { ok: true, model_count: 1 } })[0].text).toBe('1 model listed')
  })
})

describe('errors', () => {
  it('shows the server sentence for too-soon, stale and not-found; generic text otherwise', () => {
    expect(modelHealthError(new ApiError(429, { code: 'rate_limited', message: 'Models were checked less than a minute ago.' }))).toBe(
      'Models were checked less than a minute ago.',
    )
    expect(modelHealthError(new ApiError(409, { code: 'conflict', message: "The preset's model changed since you looked; refresh and try again." }))).toBe(
      "The preset's model changed since you looked; refresh and try again.",
    )
    expect(modelHealthError(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe('This only works on the main PC.')
    expect(modelHealthError(new ApiError(500, { code: 'internal_error', message: '/home/u/secret.py' }))).toBe(
      'Something went wrong inside Baihe. Details are in the app log.',
    )
    expect(modelHealthError(new ApiError(409, { code: 'conflict', message: 'see /home/user/x' }))).toBe(
      'That cannot be done right now because something else is already using it.',
    )
  })
})
