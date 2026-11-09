/*
 * The chat of the shown Live session (GET /api/live/sessions/{id}/chat), read
 * every POLL_MS while the session runs and once more after it ends, so the
 * last translations land. Messages merge by id; a read from before the oldest
 * pending one lets it fill in.
 */
import { useEffect, useRef, useState } from 'react'

import { hasStatus } from '../../api/client'
import { CHAT_KEPT, POLL_MS, chatReadFrom, getLiveChat, mergeChat } from '../../api/live'
import type { LiveChatMessage } from '../../types/live'

/**
 * `baseline`: the newest message id in the first read, i.e. what was there before this panel opened.
 * `seenAt`: when this browser first saw each kept message (ms), the clock the picture-delay hold starts from.
 */
export type ChatView = { state: string; message: string; messages: LiveChatMessage[]; baseline?: number; seenAt?: ReadonlyMap<number, number> }

const EMPTY: ChatView = { state: 'connecting', message: '', messages: [] }
// Chat states after which nothing more arrives.
const FINAL = new Set(['unavailable', 'lost', 'ended'])

export function useLiveChat(sessionId: string | null, running: boolean): ChatView {
  // Keyed by session, so a view left from another session is never shown.
  const [shown, setShown] = useState<{ id: string; view: ChatView } | null>(null)
  const runningRef = useRef(running)
  useEffect(() => {
    runningRef.current = running
  }, [running])

  useEffect(() => {
    if (!sessionId) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let kept: LiveChatMessage[] = []
    let nextId = 0
    let baseline: number | undefined
    const seen = new Map<number, number>()
    const tick = async () => {
      // The read after the session ends is the last: pending messages are not left waiting.
      const wasRunning = runningRef.current
      let again = wasRunning
      try {
        const page = await getLiveChat(sessionId, chatReadFrom(kept, nextId))
        if (!alive) return
        kept = mergeChat(kept, page.messages).slice(-CHAT_KEPT)
        nextId = Math.max(nextId, page.next_id)
        baseline ??= page.next_id
        // Stamped here, as the page arrives, so rendering never has to read the clock or a ref.
        const arrived = Date.now()
        const live = new Set(kept.map((m) => m.id))
        for (const id of live) if (!seen.has(id)) seen.set(id, arrived)
        for (const id of seen.keys()) if (!live.has(id)) seen.delete(id)
        setShown({ id: sessionId, view: { state: page.state, message: page.message, messages: kept, baseline, seenAt: new Map(seen) } })
        if (FINAL.has(page.state)) again = false
      } catch (err) {
        // A restart (404) or a refusal ends it; a single blip is retried.
        if (hasStatus(err, 401, 403, 404)) again = false
      }
      if (alive && (again || (wasRunning && !runningRef.current))) timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [sessionId])

  return shown && shown.id === sessionId ? shown.view : EMPTY
}
