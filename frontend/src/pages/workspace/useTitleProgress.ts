// Owns the counts the Simple view's steps column is drawn from, shared by the title page and the stage pages beside it.
import { useEffect, useState } from 'react'

import { getTranscribeConfig, getWorkflowProgress } from '../../api/workspace'

export function useTitleProgress(id: number, comic: boolean) {
  // Unknown until answered; with no lines yet, the main button starts at Transcribe.
  const [lineCount, setLineCount] = useState<number | null>(null)
  const [translatedCount, setTranslatedCount] = useState(0)
  const [exported, setExported] = useState(false)
  // Audio or video already attached: only then can one button transcribe.
  const [audioReady, setAudioReady] = useState(false)
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
    getTranscribeConfig(id).then((c) => !cancelled && setAudioReady(c.audio_available), () => undefined)
    return () => {
      cancelled = true
    }
  }, [id, comic])

  const onFlowProgress = () => {
    setFlowOn(true)
    setTick((t) => t + 1)
  }
  const refresh = () => setTick((t) => t + 1)
  return { lineCount, translatedCount, exported, audioReady, flowOn, onFlowProgress, refresh }
}
