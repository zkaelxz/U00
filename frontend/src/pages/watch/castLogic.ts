// When the Watch page can offer Cast to TV, and the address the TV is given.
import type { CastStatus } from '../../types/cast'

const LOOPBACK = /^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?)$/

/** A TV reaches the PC only by its public address, never the loopback one the PC's own window uses. */
export const reachableByTv = (hostname: string) => !LOOPBACK.test(hostname)

export const castUrl = (origin: string, path: string) => `${origin}${path}`

export function castLabel(state: CastStatus['state'] | null): string {
  switch (state) {
    case 'ready':
      return 'The English-subtitled copy is ready.'
    case 'working':
      return 'Making the English-subtitled copy. This takes a while.'
    case 'unsupported':
      return 'The subtitled copy is in a format most TVs refuse (not .mp4). Make it again from an .mp4 source.'
    case 'missing':
      return 'A TV needs a copy with the English subtitles burned in. Make it once, then cast.'
    default:
      return ''
  }
}
