// Where a watch party's sound should be, and when a phone is far enough off to be moved. No React, so it is unit-tested.
import type { WatchPartyRoom } from '../../api/watchParty'

/** Off by more than this and the phone is moved; closer is left alone, since a seek is heard as a skip. */
export const DRIFT_SECONDS = 1.2

/** The server clock minus this phone's clock, in seconds, taken when a state arrives. */
export const clockOffset = (room: Pick<WatchPartyRoom, 'now'>, localNowMs: number): number => room.now - localNowMs / 1000

/** Where the sound should be now: the stated position, plus the time played since it was true while it plays. */
export function expectedPosition(room: Pick<WatchPartyRoom, 'playing' | 'position' | 'at'>, offset: number, localNowMs: number): number {
  if (!room.playing) return room.position
  return room.position + Math.max(0, localNowMs / 1000 + offset - room.at)
}

export interface MediaNow {
  paused: boolean
  currentTime: number
}

export interface PartyMove {
  seekTo: number | null
  play: boolean
  pause: boolean
}

/** What this phone should do to match the party. */
export function movesToMatch(room: Pick<WatchPartyRoom, 'playing' | 'position' | 'at'>, offset: number, localNowMs: number, media: MediaNow): PartyMove {
  const want = expectedPosition(room, offset, localNowMs)
  return {
    seekTo: Math.abs(media.currentTime - want) > DRIFT_SECONDS ? want : null,
    play: room.playing && media.paused,
    pause: !room.playing && !media.paused,
  }
}
