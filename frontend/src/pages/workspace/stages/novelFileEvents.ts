// A module-level "novel files changed" counter, so panels that show the same
// file stay in step without threading a reload key through their parents:
// NovelFilePanel bumps it after a save or removal, and NovelPanel's
// "Build a glossary from this novel" link re-reads the raw-novel status.
import { useSyncExternalStore } from 'react'

let version = 0
const listeners = new Set<() => void>()

export function bumpNovelFiles(): void {
  version += 1
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

const read = () => version

export function useNovelFilesVersion(): number {
  return useSyncExternalStore(subscribe, read, read)
}
