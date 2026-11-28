/** The quiet ◀ / ▶ beside a line heard mostly in one ear of a stereo title. */
import type { EarSide } from '../../types/reader'

export function EarMark({ side }: { side: EarSide | null | undefined }) {
  if (!side) return null
  const ear = side === 'left' ? 'left ear' : 'right ear'
  return <span className="watch-ear" role="img" aria-label={side === 'left' ? 'Left ear' : 'Right ear'} title={`Heard in the ${ear}`}>{side === 'left' ? '◀' : '▶'}</span>
}
