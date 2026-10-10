import { describe, expect, it } from 'vitest'

import type { LoadedModels } from '../../api/loadedModels'
import { gpuLine, llamaLine, loadedRows, memoryLine, ollamaWhere, refreshedLine, unreadableReserveNote } from './loadedModelsModel'

const base: LoadedModels = {
  checked_at: '2026-10-07T10:00:00+00:00',
  ollama: { state: 'running', models: [] },
  app: { state: 'ok', models: [] },
  gpu: { state: 'unknown' },
  memory: { vram: { state: 'unknown', total_bytes: null, free_bytes: null, reserved_bytes: 0 }, ram: { state: 'unknown', total_bytes: null, free_bytes: null, reserved_bytes: 0 } },
  llama_cpp_running: false,
  gpu_job_running: false,
}

describe('ollamaWhere', () => {
  it('says GPU, CPU or the split', () => {
    expect(ollamaWhere(100, 100)).toBe('Ollama · GPU')
    expect(ollamaWhere(100, 0)).toBe('Ollama · CPU')
    expect(ollamaWhere(100, 60)).toBe('Ollama · 60% GPU / 40% CPU')
    expect(ollamaWhere(0, 0)).toBe('Ollama · CPU')
  })
})

describe('loadedRows', () => {
  it('lists Ollama then app models', () => {
    const rows = loadedRows({
      ...base,
      ollama: { state: 'running', models: [{ name: 'gemma4:12b', size_bytes: 8_000_000_000, vram_bytes: 8_000_000_000 }] },
      app: { state: 'ok', models: [{ name: 'large-v3-turbo', kind: 'Whisper', device: 'GPU' }, { name: 'Qwen3 forced aligner', kind: 'Alignment', device: 'Unknown' }] },
    })
    expect(rows.map((r) => [r.name, r.where, r.size])).toEqual([
      ['gemma4:12b', 'Ollama · GPU', '8.0 GB'],
      ['large-v3-turbo', 'This app · GPU', '—'],
      ['Qwen3 forced aligner', 'This app · device unknown', '—'],
    ])
  })
  it('is empty when nothing is loaded', () => expect(loadedRows(base)).toEqual([]))
})

describe('gpuLine / llamaLine / refreshedLine', () => {
  it('is honest about unknown GPU', () => expect(gpuLine({ state: 'unknown' })).toBe('GPU info unavailable.'))
  it('formats memory', () =>
    expect(gpuLine({ state: 'ok', name: 'RTX', total_bytes: 24_000_000_000, used_bytes: 8_000_000_000, free_bytes: 16_000_000_000 }))
      .toBe('RTX: 8.0 GB used of 24.0 GB (16.0 GB free)'))
  it('llama.cpp', () => {
    expect(llamaLine(true)).toBe('llama.cpp server: running')
    expect(llamaLine(false)).toBe('llama.cpp server: not found')
  })
  it('survives a bad timestamp', () => expect(refreshedLine('nope')).toBe('Last refreshed just now'))
})

describe('memoryLine', () => {
  const ok = { state: 'ok' as const, total_bytes: 32_000_000_000, free_bytes: 20_000_000_000, reserved_bytes: 0 }
  const unknown = { state: 'unknown' as const, total_bytes: null, free_bytes: null, reserved_bytes: 0 }

  it('shows free RAM and the memory kept free', () => {
    expect(memoryLine({ vram: { ...ok, reserved_bytes: 4_000_000_000 }, ram: { ...ok, reserved_bytes: 2_000_000_000 } }))
      .toMatch(/^RAM: .* free of .*\. .* of graphics memory kept free, .* of RAM kept free\.$/)
  })

  it('stays short when nothing is reserved or RAM cannot be read', () => {
    expect(memoryLine({ vram: ok, ram: ok })).not.toContain('kept free')
    expect(memoryLine({ vram: unknown, ram: unknown })).toBe('RAM info unavailable.')
  })

  it('says so when a reserve is set but that memory cannot be read', () => {
    expect(unreadableReserveNote({ vram: ok, ram: ok })).toBe('')
    expect(unreadableReserveNote({ vram: ok, ram: { ...unknown, reserved_bytes: 1 } })).toContain('RAM')
  })
})
