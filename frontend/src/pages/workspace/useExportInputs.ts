import { useEffect, useState } from 'react'

import { getSetupChecks } from '../../api/diagnostics'
import { getMediaStatus, getWorkflowProgress } from '../../api/workspace'
import type { ExportInputs } from './exportBlocks'

type Loaded = Omit<ExportInputs, 'novelNarration'>
const UNKNOWN: Loaded = { hasSourceVideo: null, hasDub: null, hasNarration: null, ffmpegFound: null }

/**
 * What decides whether each export can run. Every read is advisory: one that fails (or is not allowed for
 * this viewer) leaves its answer null, so the button stays enabled and the server's own reason shows.
 */
export function useExportInputs(dramaId: number, novelNarration: boolean): ExportInputs {
  const [loaded, setLoaded] = useState<Loaded>(UNKNOWN)
  useEffect(() => {
    let cancelled = false
    const put = (patch: Partial<Loaded>) => !cancelled && setLoaded((l) => ({ ...l, ...patch }))
    setLoaded(UNKNOWN)
    getWorkflowProgress(dramaId).then(
      (p) => put({ hasNarration: p.has_narration_track === true, hasDub: p.has_dub_track }),
      () => undefined,
    )
    getMediaStatus(dramaId).then((m) => put({ hasSourceVideo: m.has_source_video }), () => undefined)
    getSetupChecks().then((c) => put({ ffmpegFound: c.ffmpeg.found }), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId])
  return { ...loaded, novelNarration }
}
