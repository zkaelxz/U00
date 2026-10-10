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

/** RAM now, plus what Settings keeps free of it and of graphics memory. */
export function memoryLine(m: LoadedModels['memory']): string {
  const ram = m.ram.state === 'ok' && m.ram.total_bytes != null && m.ram.free_bytes != null
    ? `RAM: ${formatBytes(m.ram.free_bytes)} free of ${formatBytes(m.ram.total_bytes)}`
    : 'RAM info unavailable'
  const reserved = (name: string, k: LoadedModels['memory']['vram']) =>
    k.reserved_bytes > 0 ? `${formatBytes(k.reserved_bytes)} of ${name} kept free` : ''
  const kept = [reserved('graphics memory', m.vram), reserved('RAM', m.ram)].filter(Boolean)
  return kept.length ? `${ram}. ${kept.join(', ')}.` : `${ram}.`
}

/** Said when a reserve is set but that memory can't be read, so the setting does nothing. */
export function unreadableReserveNote(m: LoadedModels['memory']): string {
  const names = (['vram', 'ram'] as const)
    .filter((k) => m[k].reserved_bytes > 0 && m[k].state !== 'ok')
    .map((k) => (k === 'vram' ? 'graphics memory' : 'RAM'))
  return names.length ? `Can't read ${names.join(' or ')} here, so the keep-free setting is not enforced for it.` : ''
}

export const llamaLine = (running: boolean) => `llama.cpp server: ${running ? 'running' : 'not found'}`

export function refreshedLine(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'Last refreshed just now' : `Last refreshed ${d.toLocaleTimeString()}`
}

export const FREE_BUSY_NOTE = 'A transcription or other GPU job is running; freeing is paused until it finishes.'
