import { lazy, Suspense, type RefObject } from 'react'

import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import type { PlayerHandle } from './Player'
import type { Edge } from './Waveform'

// Loaded on first use so the canvas code stays out of the main bundle.
const Waveform = lazy(() => import('./Waveform'))

interface Props {
  dramaId: number
  lines: ReviewLine[]
  active: ReviewLine | null
  player: RefObject<PlayerHandle | null>
  open: boolean
  onToggle: () => void
  onRetime: (id: number, edge: Edge, value: number) => Promise<boolean>
  editingActive: boolean
}

export function ReviewWaveform({ dramaId, lines, active, player, open, onToggle, onRetime, editingActive }: Props) {
  return (
    <div className="review-wave-section">
      <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={open} onClick={onToggle}>
        {open ? 'Hide waveform' : 'Show waveform'}
      </button>
      {open && (
        <Suspense fallback={null}>
          <Waveform dramaId={dramaId} lines={lines} active={active} player={player} onRetime={onRetime} editingActive={editingActive} />
        </Suspense>
      )}
    </div>
  )
}
