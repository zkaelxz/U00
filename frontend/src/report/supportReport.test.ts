import { describe, expect, it } from 'vitest'

import { parseSupportReport, supportReportFileName } from './supportReport'

const REPORT = [
  'Python: 3.12.4',
  'ffmpeg: found (ffmpeg version 6.1)',
  'Library writable: True',
  'API keys set: none',
  'Model/engine versions:',
  '  - faster-whisper: 1.1.0',
  '  - Qwen3-ASR: not installed',
  'OS: Windows 11 (AMD64)',
  'Recent errors:',
  '  12:01 ERROR boom: File "x.py", line 3',
  '  12:02 ERROR again',
  '',
].join('\n')

describe('parseSupportReport', () => {
  it('turns "Key: value" lines into rows, with Yes/No for True/False', () => {
    const rows = parseSupportReport(REPORT)
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['Python', '3.12.4'],
      ['ffmpeg', 'found (ffmpeg version 6.1)'],
      ['Library writable', 'Yes'],
      ['API keys set', 'none'],
      ['Model/engine versions', ''],
      ['OS', 'Windows 11 (AMD64)'],
      ['Recent errors', ''],
    ])
  })

  it('nests "  - name: version" lines under their heading', () => {
    const engines = parseSupportReport(REPORT).find((r) => r.label === 'Model/engine versions')!
    expect(engines.mono).toBe(false)
    expect(engines.items).toEqual([
      { name: 'faster-whisper', value: '1.1.0' },
      { name: 'Qwen3-ASR', value: 'not installed' },
    ])
  })

  it('keeps recent error lines whole and marks them monospace', () => {
    const errors = parseSupportReport(REPORT).find((r) => r.label === 'Recent errors')!
    expect(errors.mono).toBe(true)
    expect(errors.items).toEqual([
      { name: null, value: '12:01 ERROR boom: File "x.py", line 3' },
      { name: null, value: '12:02 ERROR again' },
    ])
    expect(parseSupportReport('Recent errors: none')[0]).toMatchObject({ value: 'none', items: [] })
  })

  it('keeps a line that is not "Key: value" as its own row', () => {
    expect(parseSupportReport('Baihe report\nPython: 3.12')).toEqual([
      { label: '', value: 'Baihe report', items: [], mono: false },
      { label: 'Python', value: '3.12', items: [], mono: false },
    ])
  })
})

describe('supportReportFileName', () => {
  it('uses the local date', () => {
    expect(supportReportFileName(new Date(2026, 8, 3, 23, 59))).toBe('baihe-support-report-2026-09-03.txt')
  })
})
