import { describe, expect, it } from 'vitest'

import { scanBanner, scanBannerText, sourceToShow, type Scans } from './glossaryScan'

const idle: Scans = { novel: null, lines: null }
const done = (n: number) => ({ status: 'done', proposals: new Array(n).fill({}) })

describe('which source the Suggest-terms bar shows', () => {
  it('shows a running scan even when the other source is idle and the default is the other one', () => {
    const scans: Scans = { novel: null, lines: { status: 'running' } }
    expect(sourceToShow({ userPick: null, storedPick: null, scans, defaultSource: 'novel' })).toBe('lines')
    expect(sourceToShow({ userPick: null, storedPick: 'novel', scans, defaultSource: 'novel' })).toBe('lines')
  })

  it('shows a finished scan with proposals to review', () => {
    expect(sourceToShow({ userPick: null, storedPick: null, scans: { novel: done(3), lines: null }, defaultSource: 'lines' })).toBe('novel')
  })

  it('does not treat a finished scan with no proposals as one to show', () => {
    expect(sourceToShow({ userPick: null, storedPick: null, scans: { novel: done(0), lines: null }, defaultSource: 'lines' })).toBe('lines')
  })

  it('uses the remembered source, then the default, when nothing is held', () => {
    expect(sourceToShow({ userPick: null, storedPick: 'novel', scans: idle, defaultSource: 'lines' })).toBe('novel')
    expect(sourceToShow({ userPick: null, storedPick: null, scans: idle, defaultSource: 'lines' })).toBe('lines')
  })

  it('a choice made on this visit beats a scan on the other source', () => {
    const scans: Scans = { novel: null, lines: { status: 'running' } }
    expect(sourceToShow({ userPick: 'novel', storedPick: null, scans, defaultSource: 'lines' })).toBe('novel')
  })

  it('with both held, a running scan beats a finished one', () => {
    expect(sourceToShow({ userPick: null, storedPick: 'novel', scans: { novel: done(2), lines: { status: 'running' } }, defaultSource: 'novel' })).toBe('lines')
  })
})

describe('the scan banner', () => {
  it('is absent with nothing held, and for a finished-empty or cancelled scan', () => {
    expect(scanBanner(idle)).toBeNull()
    expect(scanBanner({ novel: done(0), lines: { status: 'cancelled' } })).toBeNull()
  })

  it('says a scan is running', () => {
    const b = scanBanner({ novel: null, lines: { status: 'queued' } })
    expect(b).toEqual({ kind: 'running', source: 'lines', count: 0 })
    expect(scanBannerText(b!)).toBe('A glossary scan is running')
  })

  it('counts the proposals of a finished scan', () => {
    expect(scanBannerText(scanBanner({ novel: done(1), lines: null })!)).toBe('Scan finished: 1 proposal to review')
    expect(scanBannerText(scanBanner({ novel: done(4), lines: null })!)).toBe('Scan finished: 4 proposals to review')
  })

  it('reports a failed scan', () => {
    expect(scanBannerText(scanBanner({ novel: { status: 'error' }, lines: null })!)).toBe('The glossary scan failed')
  })

  it('prefers running over finished', () => {
    expect(scanBanner({ novel: done(2), lines: { status: 'running' } })?.kind).toBe('running')
  })
})
