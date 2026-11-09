import { describe, expect, it } from 'vitest'

import type { ProcessMemory, ProcessRow } from '../../api/processMemory'
import { gpuGapNote, summaryLine, vramCell } from './processMemoryModel'

const row = (o: Partial<ProcessRow>): ProcessRow => ({
  pid: 1, parent_pid: 0, role: 'Python', relation: 'child', ram_bytes: 1_000_000_000, vram_bytes: null, command: 'python', ...o,
})
const base: ProcessMemory = {
  checked_at: '2026-11-09T10:00:00+00:00', state: 'ok', server_pid: 1, processes: [],
  gpu: { state: 'ok', name: 'RTX', total_bytes: 12e9, used_bytes: 10e9, free_bytes: 2e9 }, vram_attributed_bytes: null,
}

describe('process memory model', () => {
  it('says when Windows gives no per-process graphics memory', () => {
    expect(vramCell(row({}))).toBe("Windows doesn't say")
    expect(vramCell(row({ vram_bytes: 4_400_000_000 }))).toBe('4.4 GB')
  })
  it('counts processes and flags ones this server did not start', () => {
    const d = { ...base, processes: [row({}), row({ pid: 2, relation: 'orphan' })] }
    expect(summaryLine(d)).toBe('2 processes · 2.0 GB RAM · 1 not started by this server')
  })
  it('explains graphics memory no process accounts for', () => {
    expect(gpuGapNote(base)).toContain("doesn't report it per process")
    expect(gpuGapNote({ ...base, vram_attributed_bytes: 4e9 })).toContain('6.0 GB')
    expect(gpuGapNote({ ...base, vram_attributed_bytes: 9.8e9 })).toBe('')
    expect(gpuGapNote({ ...base, gpu: { state: 'unknown' } })).toBe('')
  })
})
