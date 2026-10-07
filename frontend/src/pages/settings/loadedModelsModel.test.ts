import { describe, expect, it } from 'vitest'

import type { LoadedModels } from '../../api/loadedModels'
import { gpuLine, llamaLine, loadedRows, ollamaWhere, refreshedLine } from './loadedModelsModel'

const base: LoadedModels = {
  checked_at: '2026-10-07T10:00:00+00:00',
  ollama: { state: 'running', models: [] },
  app: { state: 'ok', models: [] },
  gpu: { state: 'unknown' },
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
      app: { state: 'ok', models: [{ name: 'large-v3-turbo', kind: 'Whisper', device: 'GPU' }, { name: 'nllb', kind: 'Translation', device: 'Unknown' }] },
    })
    expect(rows.map((r) => [r.name, r.where, r.size])).toEqual([
      ['gemma4:12b', 'Ollama · GPU', '8.0 GB'],
      ['large-v3-turbo', 'This app · GPU', '—'],
      ['nllb', 'This app · device unknown', '—'],
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
