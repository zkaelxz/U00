// Watch party API (/api/watch-party, api/routers/watch_party_routes.py): one account's phones playing in step.
import type { components } from './generated/schema'
import { apiUrl, deleteJson, postJson } from './client'

export type WatchPartyRoom = components['schemas']['WatchPartyRoom']

const root = '/api/watch-party/rooms'

export const watchPartyApi = {
  create: (dramaId: number) => postJson<WatchPartyRoom>(root, { drama_id: dramaId }),
  setState: (code: string, playing: boolean, position: number) =>
    postJson<WatchPartyRoom>(`${root}/${code}/state`, { playing, position }),
  end: (code: string) => deleteJson<{ ended: boolean }>(`${root}/${code}`),
}

/**
 * Follows one party's server-sent events (a GET, so no CSRF token or local header to add). A refused stream (the code
 * is gone, or not this account's) never reconnects, which is `onEnded`; a dropped one reconnects by itself.
 * Returns the function that closes it.
 */
export function followWatchParty(code: string, on: { onState: (room: WatchPartyRoom) => void; onEnded: () => void }): () => void {
  const url = apiUrl(`${root}/${code}/stream`)
  const source = new EventSource(url)
  source.addEventListener('state', (e) => on.onState(JSON.parse((e as MessageEvent<string>).data) as WatchPartyRoom))
  source.addEventListener('ended', () => {
    source.close()
    on.onEnded()
  })
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) on.onEnded()
  }
  return () => source.close()
}
