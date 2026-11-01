import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../../api/client'
import { applyHardSubReading, estimateHardSubReading, getHardSubReading, startHardSubReading } from '../../../api/benchmark'
import { translateApi } from '../../../api/translate'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { usePersistedState } from '../../../hooks/usePersistedState'
import type { HardSubApplyResult, HardSubReadingEstimate } from '../../../types/benchmark'
import type { TranslateEngine } from '../../../types/translate'
import { formatCost } from '../../benchmark/benchmarkForm'
import { useStage } from '../StageContext'
import { canStartReading, captionEngine, READING_POLL_MS, readyVisionEngines } from './captionSpeakers'

/** Transcribe → read the burned-in captions by speaker colour and put them on the lines as speakers. PC only. */
export function CaptionSpeakers({ hasVideo, hasLines, busy }: { hasVideo: boolean; hasLines: boolean; busy: boolean }) {
  const { dramaId, refetchDrama } = useStage()
  const pc = usePcOnly()
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [remembered, setRemembered] = usePersistedState('transcribe.captionSpeakers.engine', '')
  const [estimated, setEstimated] = useState<HardSubReadingEstimate | null>(null)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [applied, setApplied] = useState<HardSubApplyResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const show = pc !== 'remote' && hasVideo && hasLines
  useEffect(() => {
    if (!show) return
    translateApi.engines().then((all) => alive.current && setEngines(all), () => {})
  }, [show])

  const engine = captionEngine(engines, remembered)
  // An estimate for another engine is not shown or used.
  const estimate = estimated?.engine === engine ? estimated : null
  useEffect(() => {
    if (!show || !engine) return
    let live = true
    estimateHardSubReading(dramaId, { engine }).then(
      (e) => live && (setEstimated(e), setError(null)),
      (e) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [show, engine, dramaId])

  if (!show) return null

  const poll = (): Promise<void> =>
    getHardSubReading(dramaId).then(async (r) => {
      if (!alive.current) return
      if (r.status === 'running') {
        setMessage(r.message)
        await new Promise((resolve) => setTimeout(resolve, READING_POLL_MS))
        return poll()
      }
      if (r.status !== 'done' || !r.result) throw new Error(r.message || 'The reading did not finish.')
      if (r.result.stopped) throw new Error(r.result.note || `The reading stopped (${r.result.stopped}).`)
      const done = await applyHardSubReading(dramaId)
      if (!alive.current) return
      setApplied(done)
      refetchDrama()
    })

  const start = () => {
    setRunning(true)
    setError(null)
    setApplied(null)
    startHardSubReading(dramaId, { engine })
      .then(() => poll())
      .catch((e: unknown) => alive.current && setError(e instanceof ApiError || e instanceof Error ? e : new Error('Reading failed.')))
      .finally(() => alive.current && (setRunning(false), setMessage(null)))
  }

  const ready = readyVisionEngines(engines)
  return (
    <Section title="Read on-screen captions by speaker" storageKey="transcribe.captionSpeakers">
      <div className="field-row">
        {ready.length > 1 && (
          <Field label="Engine">
            <select value={engine} onChange={(e) => setRemembered(e.target.value)} disabled={running}>
              {ready.map((e) => (
                <option key={e.name} value={e.name}>{e.label}</option>
              ))}
            </select>
          </Field>
        )}
        <ConfirmButton
          name="on-screen captions by speaker"
          verb="read"
          label="Read on-screen captions by speaker"
          tone="primary"
          busy={running}
          disabled={busy || !engine || !canStartReading(estimate)}
          onConfirm={start}
        />
      </div>
      {estimate && <p className="muted">{formatCost(estimate.estimated_usd)} at most</p>}
      {running && <p role="status">{message || 'Reading…'}</p>}
      {applied && <p role="status">{applied.labelled} lines labelled, {applied.unplaced} left as they were.</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
