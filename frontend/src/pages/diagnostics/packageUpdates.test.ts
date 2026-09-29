import { describe, expect, it } from 'vitest'

import type { DiagnosticsPackageUpdate } from '../../types/diagnostics'
import { canUpdate, markUpdated, updateLine, updatesSummary, versionLabel } from './packageUpdates'

const u = (o: Partial<DiagnosticsPackageUpdate>): DiagnosticsPackageUpdate => ({
  name: 'jieba', dist: 'jieba', installed_version: '0.42.0', status: 'up_to_date', latest: '0.42.0',
  target: null, reason: null, ...o,
})

describe('packageUpdates', () => {
  it('labels versions', () => {
    expect(versionLabel('1.2.3')).toBe('v1.2.3')
    expect(versionLabel(null)).toBeNull()
  })

  it('says what a check found', () => {
    expect(updateLine(u({}))).toEqual({ text: 'Up to date', tone: 'ok' })
    expect(updateLine(u({ status: 'update', target: '0.42.1', latest: '0.42.1' })).text).toBe('Update to 0.42.1 available')
    expect(updateLine(u({
      status: 'update', target: '5.2.0', latest: '6.0.0', reason: 'held back by constraints.txt (transformers<6)',
    })).text).toBe('Update to 5.2.0 available (newer 6.0.0 is held back by constraints.txt (transformers<6))')
    expect(updateLine(u({ status: 'held_back', latest: '2.0.0', reason: 'held back by transformers (needs huggingface_hub <2.0)' })))
      .toEqual({ text: 'Newer 2.0.0 exists but is held back by transformers (needs huggingface_hub <2.0)', tone: 'warn' })
    expect(updateLine(u({ status: 'managed' })).text).toContain('GPU PyTorch')
    expect(updateLine(u({ status: 'unknown' })).text).toContain("Couldn't check")
  })

  it('offers Update only for an allowed target', () => {
    expect(canUpdate(undefined)).toBe(false)
    expect(canUpdate(u({}))).toBe(false)
    expect(canUpdate(u({ status: 'held_back', latest: '2.0' }))).toBe(false)
    expect(canUpdate(u({ status: 'update', target: '0.42.1' }))).toBe(true)
  })

  it('summarises and updates after an Update', () => {
    const r = {
      checked_at: 1,
      packages: {
        a: u({ name: 'a', status: 'update', target: '2', latest: '2' }),
        b: u({ name: 'b', status: 'held_back', latest: '3' }),
        c: u({ name: 'c' }),
      },
    }
    expect(updatesSummary(r)).toBe('1 update available, 1 held back')
    const after = markUpdated(r, 'a')
    expect(after.packages.a).toMatchObject({ status: 'up_to_date', installed_version: '2', target: null })
    expect(updatesSummary(after)).toBe('1 held back')
    expect(updatesSummary({ checked_at: 1, packages: { c: u({}) } })).toBe('Everything is up to date')
  })
})
