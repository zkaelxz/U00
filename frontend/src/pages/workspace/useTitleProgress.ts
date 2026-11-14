// Owns the counts the Simple view's steps column is drawn from, shared by the title page and the stage pages beside it.
import { useCallback, useEffect, useState } from 'react'

import { listLines } from '../../api/review'
import { getTranscribeConfig, getWorkflowProgress } from '../../api/workspace'

export function useTitleProgress(id: number, comic: boolean) {
  // Unknown until answered; with no lines yet, the main button starts at Transcribe.
  const [lineCount, setLineCount] = useState<number | null>(null)
  const [translatedCount, setTranslatedCount] = useState(0)
  const [exported, setExported] = useState(false)
  // Audio or video already attached: only then can one button transcribe.
  const [audioReady, setAudioReady] = useState(false)
  // The check queue's size names the Check step's button; null until known, and a comic has no lines to check.
  const [riskCount, setRiskCount] = useState<number | null>(null)
  const [tick, setTick] = useState(0)
  // Once started the flow keeps its button even after lines exist, or the progress text would vanish mid-run.
  const [flowOn, setFlowOn] = useState(false)

  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(id).then(
      (p) => {
        if (cancelled) return
        setLineCount(p.line_count)
        setTranslatedCount(p.line_count - p.untranslated_count)
        setExported(p.exported)
      },
      () => !cancelled && setLineCount(null),
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  useEffect(() => {
    if (comic) return
    let cancelled = false
    listLines(id, 1, 1, 'risk').then(
      (r) => !cancelled && setRiskCount(typeof r.risk_count === 'number' ? r.risk_count : null),
      () => !cancelled && setRiskCount(null),
    )
    return () => {
      cancelled = true
    }
  }, [id, comic, tick])

  useEffect(() => {
    if (comic) return
    let cancelled = false
    getTranscribeConfig(id).then((c) => !cancelled && setAudioReady(c.audio_available), () => undefined)
    return () => {
      cancelled = true
    }
  }, [id, comic])

  const onFlowProgress = () => {
    setFlowOn(true)
    setTick((t) => t + 1)
  }
  // Stable, so a stage page can refresh the counts after every write without remaking its context.
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  return { lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress, refresh }
}
