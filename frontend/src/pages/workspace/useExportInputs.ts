import { useEffect, useState } from 'react'

import { getSetupChecks } from '../../api/diagnostics'
import { getMediaStatus, getWorkflowProgress } from '../../api/workspace'
import type { ExportInputs } from './exportBlocks'
import type { TitleKind } from './titleKind'

type Answers = Omit<ExportInputs, 'kind' | 'novelNarration'>
// `answered` counts the reads that came back for `dramaId`, failed ones included.
type Loaded = Answers & { dramaId: number; answered: number }
const READS = 3
const unknown = (dramaId: number): Loaded => ({ dramaId, answered: 0, hasSourceVideo: null, hasDub: null, hasNarration: null, ffmpegFound: null })

/**
 * What decides whether each export can run. Every read is advisory: one that fails (or is not allowed for
 * this viewer) leaves its answer null, so the button stays enabled and the server's own reason shows.
 */
export function useExportInputs(dramaId: number, kind: TitleKind, novelNarration: boolean): { inputs: ExportInputs; settled: boolean } {
  const [loaded, setLoaded] = useState<Loaded>(() => unknown(dramaId))
  useEffect(() => {
    let cancelled = false
    const put = (patch: Partial<Answers>) => !cancelled && setLoaded((l) => ({ ...l, ...patch, answered: l.answered + 1 }))
    setLoaded(unknown(dramaId))
    getWorkflowProgress(dramaId).then(
      (p) => put({ hasNarration: p.has_narration_track === true, hasDub: p.has_dub_track }),
      () => put({}),
    )
    getMediaStatus(dramaId).then((m) => put({ hasSourceVideo: m.has_source_video }), () => put({}))
    getSetupChecks().then((c) => put({ ffmpegFound: c.ffmpeg.found }), () => put({}))
    return () => {
      cancelled = true
    }
  }, [dramaId])
  const { hasSourceVideo, hasDub, hasNarration, ffmpegFound } = loaded
  return {
    inputs: { kind, novelNarration, hasSourceVideo, hasDub, hasNarration, ffmpegFound },
    // Until the effect resets them, the answers still belong to the previous title.
    settled: loaded.dramaId === dramaId && loaded.answered === READS,
  }
}
