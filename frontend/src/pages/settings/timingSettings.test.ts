import { describe, expect, it } from 'vitest'

import type { SettingsOverview, SettingsSchemaRow } from '../../types/settings'
import { toSavedValue } from './SchemaCard'
import { defaultLine, draftText, formatValue, isDefault, resetPatch, savedNumber, timingGroups } from './timingSettings'

const row = (over: Partial<SettingsSchemaRow>): SettingsSchemaRow => ({
  key: 'split_max_seconds', type: 'float', default: 8, scope: 'app', label: 'Longest line (time)', help: 'Cut lines longer than this.',
  unit: 's', placeholder: '', tab: 'system', section: 'Line splitting', custom: false, dev_only: false, choices: null,
  min: 4, max: 20, max_len: null, multiline: false, ...over,
})

const SCHEMA = [
  row({ key: 'qwen_vad_threshold', section: 'Voice detection', dev_only: true, default: 0.5, min: 0.2, max: 0.8, unit: '' }),
  row({ key: 'split_max_seconds' }),
  row({ key: 'split_min_piece_words', type: 'int', default: 2, min: 1, max: 4, unit: 'words', dev_only: true }),
  row({ key: 'scene_break_gap_seconds', section: 'Sentence grouping', default: 3, min: 1.5, max: 15 }),
  row({ key: 'default_engine', section: 'Defaults for new dramas', type: 'choice' }),
]

describe('timingGroups', () => {
  it('lists the three groups in page order and leaves other sections out', () => {
    const groups = timingGroups(SCHEMA, true)
    expect(groups.map((g) => g.section)).toEqual(['Voice detection', 'Line splitting', 'Sentence grouping'])
    expect(groups.flatMap((g) => g.rows.map((r) => r.key))).not.toContain('default_engine')
  })

  it('keeps the declared order inside a group', () => {
    const line = timingGroups(SCHEMA, true).find((g) => g.section === 'Line splitting')
    expect(line?.rows.map((r) => r.key)).toEqual(['split_max_seconds', 'split_min_piece_words'])
  })

  it('shows a Developer Mode row only while Developer Mode is on, and drops a group left empty', () => {
    const off = timingGroups(SCHEMA, false)
    expect(off.map((g) => g.section)).toEqual(['Line splitting', 'Sentence grouping'])
    expect(off[0].rows.map((r) => r.key)).toEqual(['split_max_seconds'])
  })

  it('has no groups before the schema arrives', () => {
    expect(timingGroups(null, true)).toEqual([])
  })
})

describe('values and defaults', () => {
  it('words a value with its unit and the default beside it', () => {
    const r = row({})
    expect(formatValue(r, 8)).toBe('8 s')
    expect(defaultLine(r)).toBe('Default: 8 s')
    expect(defaultLine(row({ unit: '', default: 0.5 }))).toBe('Default: 0.5')
  })

  it('knows whether a value is the default', () => {
    expect(isDefault(row({}), 8)).toBe(true)
    expect(isDefault(row({}), 9)).toBe(false)
  })

  it('reads the saved number, falling back to the default for an older server', () => {
    const settings = { preferences: { split_max_seconds: 10 } } as unknown as SettingsOverview
    expect(savedNumber(settings, row({}))).toBe(10)
    expect(savedNumber({ preferences: {} } as unknown as SettingsOverview, row({}))).toBe(8)
  })

  it('shows a zero as 0, not blank: it is a real value for these rows', () => {
    expect(draftText(0)).toBe('0')
  })
})

describe('Reset', () => {
  it('sends the declared default for just that row', () => {
    expect(resetPatch(row({}))).toEqual({ split_max_seconds: 8 })
    expect(resetPatch(row({ key: 'qwen_vad_pad_ms', type: 'int', default: 100 }))).toEqual({ qwen_vad_pad_ms: 100 })
  })
})

describe('entering a value', () => {
  it('refuses zero and anything outside the range where the minimum is above 0', () => {
    const r = row({})
    for (const bad of ['0', '3.9', '20.1', '-1', 'abc']) expect(toSavedValue(r, bad).ok).toBe(false)
    expect(toSavedValue(r, '4')).toEqual({ ok: true, value: 4 })
    expect(toSavedValue(r, ' 12.5 ')).toEqual({ ok: true, value: 12.5 })
  })

  it('accepts zero where the minimum is 0, and a blank puts the default back', () => {
    const r = row({ key: 'qwen_vad_pad_ms', type: 'int', default: 100, min: 0, max: 500, unit: 'ms' })
    expect(toSavedValue(r, '0')).toEqual({ ok: true, value: 0 })
    expect(toSavedValue(r, '')).toEqual({ ok: true, value: 100 })
    expect(toSavedValue(r, '1.5').ok).toBe(false)
  })
})
