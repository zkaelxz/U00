/** The speakers a title already has, read from the Characters list when `enabled` turns true. */
import { useEffect, useState } from 'react'

import { getJson } from '../../../../api/client'
import type { components } from '../../../../api/generated/schema'

type SpeakerEntry = components['schemas']['CharactersEntry']

// null while loading; an empty list after a failed read, since the person can still type a name.
export function useTitleSpeakers(dramaId: number, enabled: boolean): string[] | null {
  const [known, setKnown] = useState<string[] | null>(null)
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    getJson<SpeakerEntry[]>(`/api/characters/dramas/${dramaId}`).then(
      (rows) => !cancelled && setKnown(rows.map((r) => r.speaker_label).filter(Boolean)),
      () => !cancelled && setKnown([]),
    )
    return () => {
      cancelled = true
    }
  }, [enabled, dramaId])
  return known
}
