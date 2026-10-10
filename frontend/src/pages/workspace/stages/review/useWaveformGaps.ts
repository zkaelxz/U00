import { useCallback, useEffect, useState } from 'react'

import { listAllLines } from '../../../../api/restructure'
import { addGapLines, getTranscribeGaps } from '../../../../api/workspace'
import type { TranscribeGap } from '../../../../types/workspace'

// The title's untranscribed gaps for the waveform, and "Transcribe this gap":
// add the blank flagged lines that cover it (the server cuts them at ~30 s,
// at pauses where it can), then hand their ids to the re-transcribe section.
// `refresh` is Review's reload counter: any write or finished job refetches.
export function useWaveformGaps({
  dramaId,
  enabled,
  refresh,
  onChanged,
  onTranscribe,
}: {
  dramaId: number
  enabled: boolean
  refresh: number
  onChanged: () => void
  onTranscribe: (lineIds: number[]) => void
}) {
  const [gaps, setGaps] = useState<TranscribeGap[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (!enabled) return
    const ctl = new AbortController()
    getTranscribeGaps(dramaId, ctl.signal).then(
      (r) => !ctl.signal.aborted && setGaps(r.gaps),
      () => !ctl.signal.aborted && setGaps([]),
    )
    return () => ctl.abort()
  }, [dramaId, enabled, refresh])

  const transcribe = useCallback(
    async (gap: TranscribeGap) => {
      setBusy(true)
      setError(null)
      try {
        // The whole list, not the page on screen: the server refuses the edit
        // if the title's line ids differ from these.
        const all = await listAllLines(dramaId)
        const added = await addGapLines(dramaId, {
          expected_line_ids: all.map((l) => l.id),
          start: gap.start,
          end: gap.end,
          after_line_id: gap.after_line_id,
        })
        onChanged()
        onTranscribe(added.new_line_ids)
      } catch (e) {
        setError(e)
      } finally {
        setBusy(false)
      }
    },
    [dramaId, onChanged, onTranscribe],
  )

  return { gaps, busy, error, dismissError: () => setError(null), transcribe }
}
