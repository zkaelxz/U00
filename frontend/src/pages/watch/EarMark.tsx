/** The quiet ◀ / ▶ beside a line heard mostly in one ear of a stereo title. */
import type { EarSide } from '../../types/reader'

export function EarMark({ side }: { side: EarSide | null | undefined }) {
  if (!side) return null
  const label = side === 'left' ? 'left ear' : 'right ear'
  return <span className="watch-ear" role="img" aria-label={label} title={`Heard in the ${label}`}>{side === 'left' ? '◀' : '▶'}</span>
}
