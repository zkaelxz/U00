// Owns the counts the Simple view's steps column is drawn from, shared by the title page and the stage pages beside it.
import { useCallback, useEffect, useRef, useState } from 'react'

import { getTitleFlow } from '../../api/queue'
import { listLines } from '../../api/review'
import { getTranscribeConfig, getWorkflowProgress } from '../../api/workspace'

export function useTitleProgress(id: number, comic: boolean) {
  // Unknown until answered; with no lines yet, the main button starts at Transcribe.
  const [lineCount, setLineCount] = useState<number | null>(null)
  // The progress request failed, so a null count means "can't say" rather than "not answered yet".
  const [countFailed, setCountFailed] = useState(false)
  const [translatedCount, setTranslatedCount] = useState(0)
  const [exported, setExported] = useState(false)
  // Audio or video already attached: only then can one button transcribe. Null until answered, so a slow answer
  // doesn't show "Add media" on a title whose media is there; a failed answer falls back to "not attached".
  const [audioReady, setAudioReady] = useState<boolean | null>(null)
  // The check queue's size names the Check step's button; null until known, and a comic has no lines to check.
  const [riskCount, setRiskCount] = useState<number | null>(null)
  // The check queue's size has been asked for and answered (or failed): the Check button's words are final from then on.
  const [riskAsked, setRiskAsked] = useState(false)
  const [tick, setTick] = useState(0)
  // Once started the flow keeps its button even after lines exist, or the progress text would vanish mid-run.
  const [flowOn, setFlowOn] = useState(false)
  // A refresh that fails keeps the counts already on screen: dropping them would swap the title panel for the lines and
  // take the steps' words and the main button away until the next good answer. Only a first read that fails "can't say".
  const answered = useRef(false)

  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(id).then(
      (p) => {
        if (cancelled) return
        answered.current = true
        setLineCount(p.line_count)
        setCountFailed(false)
        setTranslatedCount(p.line_count - p.untranslated_count)
        setExported(p.exported)
      },
      () => {
        if (!cancelled && !answered.current) setCountFailed(true)
      },
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  useEffect(() => {
    if (comic) return
    let cancelled = false
    listLines(id, 1, 1, 'risk').then(
      (r) => {
        if (cancelled) return
        setRiskCount(typeof r.risk_count === 'number' ? r.risk_count : null)
        setRiskAsked(true)
      },
      // Kept as it was, for the same reason as the line count; before any answer it is still null, "can't say".
      () => !cancelled && setRiskAsked(true),
    )
    return () => {
      cancelled = true
    }
  }, [id, comic, tick])

  useEffect(() => {
    if (comic) return
    let cancelled = false
    getTranscribeConfig(id).then((c) => !cancelled && setAudioReady(c.audio_available), () => !cancelled && setAudioReady(false))
    return () => {
      cancelled = true
    }
  }, [id, comic])

  // A run the PC is still carrying on keeps its button and progress when the page is opened again mid-run.
  useEffect(() => {
    if (comic) return
    let cancelled = false
    getTitleFlow(id).then((r) => {
      if (!cancelled && (r.state === 'waiting' || r.state === 'running')) setFlowOn(true)
    }, () => undefined)
    return () => {
      cancelled = true
    }
  }, [id, comic])

  const onFlowProgress = useCallback(() => {
    setFlowOn(true)
    setTick((t) => t + 1)
  }, [])
  // Stable, so a stage page can refresh the counts after every write without remaking its context.
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  return { lineCount, countFailed, translatedCount, exported, audioReady, riskCount, riskAsked, flowOn, onFlowProgress, refresh }
}
