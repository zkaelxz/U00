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

export const SHARE_CHOICES = [
  { days: 1, label: '1 day' },
  { days: 3, label: '3 days' },
  { days: 7, label: '7 days' },
] as const

export function shareNote(days: number): string {
  const word = days === 1 ? '1 day' : `${days} days`
  return `Anyone with this link can play this episode for ${word}, or until Baihe restarts. It opens nothing else.`
}
