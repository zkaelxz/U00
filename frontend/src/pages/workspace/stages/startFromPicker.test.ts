import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../api/translateStage', () => ({
  applyWorkflowTier: vi.fn(),
  applyTranslatePreset: vi.fn(),
}))
vi.mock('../translateForm', () => ({ savePresetStart: vi.fn() }))

import { applyTranslatePreset, applyWorkflowTier } from '../../../api/translateStage'
import { savePresetStart } from '../translateForm'
import { applyStart, parseStartValue, startOptions } from './StartFromPicker'

const tiers = [{ key: 'draft', label: 'Draft' }, { key: 'standard', label: 'Standard' }] as never
const presets = [{ id: 7, name: 'Wuxia' }] as never

beforeEach(() => vi.clearAllMocks())

describe('startOptions', () => {
  it('groups tiers and presets with kind-prefixed values', () => {
    expect(startOptions(tiers, presets)).toEqual({
      tiers: [{ value: 'tier:draft', label: 'Draft' }, { value: 'tier:standard', label: 'Standard' }],
      presets: [{ value: 'preset:7', label: 'Wuxia' }],
    })
  })
  it('is empty when neither list has anything, so the picker hides', () => {
    expect(startOptions(undefined, null)).toEqual({ tiers: [], presets: [] })
  })
})

describe('parseStartValue', () => {
  it('reads both kinds and rejects junk', () => {
    expect(parseStartValue('tier:draft')).toEqual({ kind: 'tier', key: 'draft' })
    expect(parseStartValue('preset:7')).toEqual({ kind: 'preset', id: 7 })
    expect(parseStartValue('')).toBeNull()
    expect(parseStartValue('preset:x')).toBeNull()
    expect(parseStartValue('tier:')).toBeNull()
  })
})

describe('applyStart', () => {
  it('applies a tier through applyWorkflowTier only', async () => {
    vi.mocked(applyWorkflowTier).mockResolvedValue({ tier: 'draft' } as never)
    const r = await applyStart(3, 'tier:draft')
    expect(applyWorkflowTier).toHaveBeenCalledWith(3, 'draft')
    expect(applyTranslatePreset).not.toHaveBeenCalled()
    expect(r).toEqual({ kind: 'tier', tier: { tier: 'draft' } })
  })
  it('applies a preset and remembers it for later visits', async () => {
    const p = { id: 7, name: 'Wuxia' }
    vi.mocked(applyTranslatePreset).mockResolvedValue(p as never)
    const r = await applyStart(3, 'preset:7')
    expect(applyTranslatePreset).toHaveBeenCalledWith(3, 7)
    expect(applyWorkflowTier).not.toHaveBeenCalled()
    expect(savePresetStart).toHaveBeenCalledWith(3, p)
    expect(r).toEqual({ kind: 'preset', preset: p })
  })
  it('does nothing for an empty choice', async () => {
    expect(await applyStart(3, '')).toBeNull()
    expect(applyWorkflowTier).not.toHaveBeenCalled()
  })
})
