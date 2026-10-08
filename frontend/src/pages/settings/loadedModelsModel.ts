// Pure helpers for the "Loaded now" card (LoadedModelsCard.tsx).
import type { LoadedModels } from '../../api/loadedModels'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

export type LoadedRow = { key: string; name: string; where: string; size: string }

export const OLLAMA_EMPTY: Record<LoadedModels['ollama']['state'], string> = {
  running: 'Nothing loaded in Ollama.',
  not_running: "Ollama isn't running.",
  not_local: 'Ollama is set to another machine, so it is not checked from here.',
  unavailable: 'Ollama is unavailable.',
}

// Where Ollama runs a model, from how much of it sits in video memory.
export function ollamaWhere(size: number, vram: number): string {
  if (!(size > 0) || !(vram > 0)) return 'Ollama · CPU'
  if (vram >= size) return 'Ollama · GPU'
  return `Ollama · ${Math.round((vram / size) * 100)}% GPU / ${Math.round(100 - (vram / size) * 100)}% CPU`
}

export function loadedRows(d: LoadedModels): LoadedRow[] {
  const ollama = d.ollama.models.map((m) => ({
    key: `ollama:${m.name}`,
    name: m.name,
    where: ollamaWhere(m.size_bytes, m.vram_bytes),
    size: formatBytes(m.size_bytes),
  }))
  const app = d.app.models.map((m, i) => ({
    key: `app:${i}:${m.name}`,
    name: m.name,
    where: `This app · ${m.device === 'Unknown' ? 'device unknown' : m.device}`,
    size: '—',
  }))
  return [...ollama, ...app]
}

export function gpuLine(g: LoadedModels['gpu']): string {
  if (g.state !== 'ok' || g.total_bytes == null || g.used_bytes == null || g.free_bytes == null) {
    return 'GPU info unavailable.'
  }
  return `${g.name ?? 'GPU'}: ${formatBytes(g.used_bytes)} used of ${formatBytes(g.total_bytes)} (${formatBytes(g.free_bytes)} free)`
}

export const llamaLine = (running: boolean) => `llama.cpp server: ${running ? 'running' : 'not found'}`

export function refreshedLine(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'Last refreshed just now' : `Last refreshed ${d.toLocaleTimeString()}`
}

export const FREE_BUSY_NOTE = 'A transcription or other GPU job is running; freeing is paused until it finishes.'
