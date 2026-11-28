/** Hover, focus and tap state for a chart's points (shown by components/ChartReadout). */
import { useState } from 'react'

export function useChartReadout() {
  const [text, setText] = useState('')
  const point = (label: string) => ({
    tabIndex: 0,
    role: 'img' as const,
    'aria-label': label,
    onMouseEnter: () => setText(label),
    onFocus: () => setText(label),
    onClick: () => setText(label),
    // A touch has no leave until the next tap, so only a real mouse clears the readout.
    onPointerLeave: (e: { pointerType: string }) => { if (e.pointerType === 'mouse') setText('') },
  })
  return { text, point }
}
