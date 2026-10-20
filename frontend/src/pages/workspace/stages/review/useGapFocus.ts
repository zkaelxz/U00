import { useState, type Dispatch, type RefObject, type SetStateAction } from 'react'

import type { TranscribeGap } from '../../../../types/workspace'
import { nextGap } from './gapModel'
import type { PlayerHandle } from './Player'
import { gapLabel } from './retranscribeLinesLogic'

interface Deps {
  gaps: TranscribeGap[]
  player: RefObject<PlayerHandle | null>
  setWaveOpen: (open: boolean) => void
  setStatus: Dispatch<SetStateAction<string | null>>
}

// Which waveform gap the toolbar's next/previous/"go to" is on. `seq` makes a repeat jump to the
// same gap count again.
export function useGapFocus({ gaps, player, setWaveOpen, setStatus }: Deps) {
  const [gapFocus, setGapFocus] = useState<{ gap: TranscribeGap; seq: number } | null>(null)
  const sortedGaps = [...gaps].sort((a, b) => a.start - b.start)
  const gapIndex = gapFocus ? sortedGaps.findIndex((g) => g.start === gapFocus.gap.start) + 1 || null : null
  const focusGap = (g: TranscribeGap | null | undefined) => {
    if (!g) return
    setWaveOpen(true)
    player.current?.seek(g.start)
    setGapFocus((f) => ({ gap: g, seq: (f?.seq ?? 0) + 1 }))
    setStatus(`Gap: ${gapLabel(g)}`)
  }
  const jumpToGap = (find: typeof nextGap) => focusGap(find(gaps, player.current?.getTime() ?? 0))
  return { gapFocus, gapIndex, sortedGaps, focusGap, jumpToGap }
}
