/** The sessions of this account that are running now, for the stream chips while one is watched. */
import { useEffect, useState } from 'react'

import { isActive, listLive } from '../../api/live'
import type { LiveSessionSummary } from '../../types/live'

const REFRESH_MS = 10_000

export function useRunningSessions(watching: string | undefined, everyone: boolean) {
  const [running, setRunning] = useState<LiveSessionSummary[]>([])
  useEffect(() => {
    if (!watching) {
      setRunning([])
      return
    }
    let alive = true
    const read = () => listLive(everyone).then((list) => {
      if (alive) setRunning(list.filter((s) => isActive(s.status)))
    }, () => {})
    void read()
    const t = setInterval(read, REFRESH_MS)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [watching, everyone])
  return running
}

const SITES: Record<string, string> = { youtube: 'YouTube', 'twitch-channel': 'Twitch', 'twitch-video': 'Twitch' }
const LANGUAGES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }

/** A chip's text: a Twitch channel by name, else the language and site; a repeat gets its number. */
export function chipLabels(list: LiveSessionSummary[]): string[] {
  const base = list.map((s) => s.stream?.kind === 'twitch-channel' ? s.stream.id
    : [LANGUAGES[s.source_language ?? ''], SITES[s.stream?.kind ?? '']].filter(Boolean).join(' · ') || 'Stream')
  const seen = new Map<string, number>()
  return base.map((label) => {
    const n = (seen.get(label) ?? 0) + 1
    seen.set(label, n)
    return n > 1 ? `${label} ${n}` : label
  })
}
