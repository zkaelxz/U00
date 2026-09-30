/*
 * useEventStream(onEvent): subscribe a component to the tab's shared push
 * stream (api/eventStream.ts). Returns { mode, syncs }:
 *   mode  'push' or 'connecting': rely on events (after the usual first GET);
 *         'poll': the stream is down, run the old polling loop.
 *   syncs bumps on every (re)open and a server 'resync': re-read with one GET
 *         (use it as an effect dependency).
 * The handler may change every render; the subscription does not.
 */
import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'

import { eventHub, type EventHub, type StreamState } from '../api/eventStream'

export function useEventStream(
  onEvent?: (type: string, data: unknown) => void,
  hub: EventHub = eventHub(),
): StreamState {
  const handler = useRef(onEvent)

  useEffect(() => {
    handler.current = onEvent
  })

  // One hub subscription per component: its events and its state changes.
  const subscribe = useCallback(
    (notify: () => void) =>
      hub.subscribe({ onEvent: (type, data) => handler.current?.(type, data), onState: notify }),
    [hub],
  )
  return useSyncExternalStore(subscribe, hub.state, hub.state)
}
