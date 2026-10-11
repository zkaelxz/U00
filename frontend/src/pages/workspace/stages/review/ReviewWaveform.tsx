import { lazy, Suspense, type RefObject } from 'react'

import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import type { PlayerHandle } from './Player'
import { gapBlockedReason } from './retranscribeLinesLogic'
import { useWaveformGaps } from './useWaveformGaps'
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
  // Review's reload counter, and what "Transcribe this gap" needs from the panel.
  reloads: number
  jobRunning: boolean
  canTranscribe: boolean
  onChanged: () => void
  onTranscribeLines: (lineIds: number[]) => void
}

export function ReviewWaveform({ dramaId, lines, active, player, open, onToggle, onRetime, editingActive, reloads, jobRunning, canTranscribe, onChanged, onTranscribeLines }: Props) {
  const gaps = useWaveformGaps({ dramaId, enabled: open && canTranscribe, refresh: reloads, onChanged, onTranscribe: onTranscribeLines })
  const blocked = gapBlockedReason({ jobRunning, editing: editingActive, canTranscribe })
  return (
    <div className="review-wave-section">
      <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={open} onClick={onToggle}>
        {open ? 'Hide waveform' : 'Show waveform'}
      </button>
      {open && (
        <Suspense fallback={null}>
          <Waveform
            dramaId={dramaId} lines={lines} active={active} player={player} onRetime={onRetime} editingActive={editingActive}
            gaps={gaps.gaps} gapBlocked={gaps.busy ? 'Adding lines for the gap…' : blocked} onTranscribeGap={(g) => void gaps.transcribe(g)}
          />
        </Suspense>
      )}
      {open && <ErrorBanner error={gaps.error} onDismiss={gaps.dismissError} />}
    </div>
  )
}
