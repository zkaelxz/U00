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
      return 'The subtitled copy is not an .mp4, which most TVs refuse. You can still save it to your phone.'
    case 'missing':
      return 'Casting and saving use a copy with the English subtitles burned in. Make it once, then cast or save.'
    default:
      return ''
  }
}
