import { lazy, Suspense, type RefObject } from 'react'

import { ErrorBanner } from '../../../../components/ErrorBanner'
import { NeedsInstall } from '../../../readiness/NeedsInstall'
import type { Need } from '../../../readiness/needs'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import type { PlayerHandle } from './Player'
import { gapBlockedReason } from './retranscribeLinesLogic'
import type { WaveformGaps } from './useWaveformGaps'
import type { TranscribeGap } from '../../../../types/workspace'
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
  // The title's gaps and "Transcribe this gap" (shared with the Gaps chip), and when the button is off.
  gaps: WaveformGaps
  jobRunning: boolean
  canTranscribe: boolean
  // A missing install (Whisper) that turns the gap buttons off, with its fix shown under the waveform.
  need?: Need | null
  focusGap: { gap: TranscribeGap; seq: number } | null
  lineCount?: number | null
}

export function ReviewWaveform({ dramaId, lines, active, player, open, onToggle, onRetime, editingActive, gaps, jobRunning, canTranscribe, need = null, focusGap, lineCount = null }: Props) {
  const blocked = gapBlockedReason({ jobRunning, editing: editingActive, canTranscribe, needReason: need?.reason })
  return (
    <div className="review-wave-section">
      <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={open} onClick={onToggle}>
        {open ? 'Hide waveform' : 'Show waveform'}
      </button>
      {open && (
        <Suspense fallback={null}>
          <Waveform
            dramaId={dramaId} lines={lines} active={active} player={player} onRetime={onRetime} editingActive={editingActive}
            gaps={gaps.gaps} gapBlocked={gaps.busy ? 'Adding lines for the gap…' : blocked} onTranscribeGap={(g) => void gaps.transcribe(g)} focusGap={focusGap} lineCount={lineCount}
          />
        </Suspense>
      )}
      {open && need && canTranscribe && gaps.gaps.length > 0 && <NeedsInstall need={need} />}
      {open && <ErrorBanner error={gaps.error} onDismiss={gaps.dismissError} />}
    </div>
  )
}
