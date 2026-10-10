// A module-level "novel files changed" counter, so panels that show the same
// file stay in step without threading a reload key through their parents:
// NovelFilePanel bumps it after a save or removal, and NovelPanel's
// "Build a glossary from this novel" link re-reads the raw-novel status.
import { useEffect, useState, useSyncExternalStore } from 'react'

import { getRawNovel } from '../../../api/novelFiles'
import { getNovelStatus } from '../../../api/workspace'
import type { NovelPresence } from './novelFile'

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

// Whether the drama has novel text or a raw novel; null while reading. A failed
// read counts as present so a title that does have a novel never loses its panels.
export function useNovelPresence(dramaId: number, reloadKey: number): NovelPresence | null {
  const [presence, setPresence] = useState<NovelPresence | null>(null)
  const filesVersion = useNovelFilesVersion()
  useEffect(() => {
    let cancelled = false
    Promise.all([getNovelStatus(dramaId), getRawNovel(dramaId)]).then(
      ([text, raw]) => !cancelled && setPresence({ text: text.has_novel_text, raw: raw.present }),
      () => !cancelled && setPresence({ text: true, raw: true }),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloadKey, filesVersion])
  return presence
}
