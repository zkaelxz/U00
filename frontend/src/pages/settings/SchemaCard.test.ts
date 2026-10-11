import { describe, expect, it } from 'vitest'

import type { SettingsSchemaRow } from '../../types/settings'
import { toDraftValue, toSavedValue } from './SchemaCard'

const row = (over: Partial<SettingsSchemaRow>): SettingsSchemaRow => ({
  key: 'k', type: 'text', default: null, scope: 'app', label: 'Thing', help: '', unit: '', placeholder: '',
  tab: 'system', section: 'Advanced', custom: false, dev_only: false, choices: null,
  min: null, max: null, max_len: null, multiline: false, ...over,
})

describe('toSavedValue', () => {
  it('floats: blank is the default (null = use .env), 0 is allowed, text and negatives are not', () => {
    const cap = row({ type: 'float', default: null, min: 0, max: 1_000_000 })
    expect(toSavedValue(cap, '')).toEqual({ ok: true, value: null })
    expect(toSavedValue(cap, ' 12.50 ')).toEqual({ ok: true, value: 12.5 })
    expect(toSavedValue(cap, '0')).toEqual({ ok: true, value: 0 })
    for (const bad of ['lots', '-1', '1e3', '2000000']) expect(toSavedValue(cap, bad).ok).toBe(false)
    expect(toSavedValue(cap, 'lots')).toMatchObject({ error: expect.stringContaining('Enter an amount') })
  })

  it('ints: whole numbers inside the declared range, blank is the default', () => {
    const upload = row({ type: 'int', default: 20480, min: 100, max: 1_048_576 })
    expect(toSavedValue(upload, '')).toEqual({ ok: true, value: 20480 })
    expect(toSavedValue(upload, ' 4096 ')).toEqual({ ok: true, value: 4096 })
    expect(toSavedValue(upload, '100').ok).toBe(true)
    expect(toSavedValue(upload, '1048576').ok).toBe(true)
    for (const bad of ['99', '0', '-5', '1048577', '1.5', '2 GB', '1e3']) expect(toSavedValue(upload, bad).ok).toBe(false)
    expect(toSavedValue(upload, '1.5')).toMatchObject({ error: 'Enter a whole number from 100 to 1,048,576.' })
  })

  it('choices fall back to the default; paths and text are checked', () => {
    expect(toSavedValue(row({ type: 'choice', default: null }), '')).toEqual({ ok: true, value: null })
    expect(toSavedValue(row({ type: 'choice', default: 'auto' }), 'tesseract')).toEqual({ ok: true, value: 'tesseract' })
    expect(toSavedValue(row({ type: 'path' }), ' C:\\a\\b.exe ')).toEqual({ ok: true, value: 'C:\\a\\b.exe' })
    expect(toSavedValue(row({ type: 'path' }), 'a\nb').ok).toBe(false)
    expect(toSavedValue(row({ type: 'text', max_len: 3 }), 'abcd').ok).toBe(false)
    expect(toSavedValue(row({ type: 'bool' }), true)).toEqual({ ok: true, value: true })
  })
})

describe('toDraftValue', () => {
  it('shows an unset or zero number as blank', () => {
    expect(toDraftValue(row({ type: 'int' }), 0)).toBe('')
    expect(toDraftValue(row({ type: 'float' }), null)).toBe('')
    expect(toDraftValue(row({ type: 'int' }), 20480)).toBe('20480')
    expect(toDraftValue(row({ type: 'choice' }), null)).toBe('')
  })
})
