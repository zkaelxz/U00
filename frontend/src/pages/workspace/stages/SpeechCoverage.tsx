import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../../api/client'
import { mediaStreamUrl } from '../../../api/media'
import { cancelJob } from '../../../api/jobs'
import { getSpeechCoverage, startSpeechCoverage } from '../../../api/speechCoverage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Section } from '../../../components/Section'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import { routeHref } from '../../../router'
import type { SpeechCoverageGap } from '../../../types/speechCoverage'
import { useStage } from '../StageContext'
import { isActiveStatus } from './autotuneGlossary'
import {
  coverageBlocker,
  coverageSummary,
  coverageTotals,
  DEFAULT_MIN_GAP_SECONDS,
  failedText,
  gapRange,
  PLAY_LEAD_SECONDS,
  rawStatusText,
} from './speechCoverageText'
import { useRunStatus } from './useRunStatus'
import './speechCoverage.css'

interface Props {
  hasAudio: boolean
  // Another job for this drama (transcribe, diarize, upload) is running.
  busy: boolean
}

// Transcribe → "Speech coverage": lists stretches of the audio where the
// speech detector hears speech but no subtitle line sits, and says whether
// Whisper's raw output had text there. Read-only; runs on the CPU.
export function SpeechCoverage({ hasAudio, busy }: Props) {
  const { dramaId } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, refresh, clearError } = useRunStatus(dramaId, getSpeechCoverage)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [cancelSentFor, setCancelSentFor] = useState<object | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const stopAt = useRef<number | null>(null)

  const active = isActiveStatus(status?.status)
  const blocker = active ? null : coverageBlocker(hasAudio, busy)
  const report = status?.status === 'done' ? status.result : null

  const start = () => {
    setStarting(true)
    setError(null)
    startSpeechCoverage(dramaId, DEFAULT_MIN_GAP_SECONDS)
      .then(
        () => refresh(),
        (e: unknown) => {
          // 409: a check is already running (another tab); attach to it.
          if (e instanceof ApiError && e.status === 409) refresh()
          else setError(e)
        },
      )
      .finally(() => setStarting(false))
  }

  // A transcription just finished: its lines are new, so check them.
  const wasBusy = useRef(busy)
  const startRef = useRef(start)
  startRef.current = start
  useEffect(() => {
    if (wasBusy.current && !busy && hasAudio) startRef.current()
    wasBusy.current = busy
  }, [busy, hasAudio])

  const cancel = () => {
    if (!status) return
    setCancelSentFor(status)
    cancelJob(status.job_id).then(refresh, (e: unknown) => {
      setCancelSentFor(null)
      setError(e)
    })
  }

  const play = (g: SpeechCoverageGap) => {
    const el = audioRef.current
    if (!el) return
    el.currentTime = Math.max(0, g.start - PLAY_LEAD_SECONDS)
    stopAt.current = g.end + PLAY_LEAD_SECONDS
    // A new seek interrupts a pending play(); that is not a failure.
    el.play().catch(() => undefined)
  }

  const reviewHref = routeHref({ name: 'drama', id: dramaId, stage: 'review' })

  const actions = (g: SpeechCoverageGap) => (
    <span className="speech-coverage-actions">
      <button type="button" onClick={() => play(g)} aria-label={`Play ${gapRange(g.start, g.end)}`}>
        Play
      </button>
      <a href={reviewHref}>Open Review</a>
    </span>
  )

  const rawNote = (g: SpeechCoverageGap) => (
    <>
      <span data-testid="gap-raw" data-raw-status={g.raw_status}>{rawStatusText(g.raw_status)}</span>
      {g.raw_text && <span className="muted"> · “{g.raw_text}”</span>}
    </>
  )

  const summary = active ? 'running' : report ? coverageSummary(report) : 'speech with no subtitle line'

  return (
    <Section storageKey="source.speechCoverage" title="Speech coverage" summary={summary}>
      <div className="speech-coverage" data-testid="speech-coverage">
        <p className="muted">
          Finds stretches where speech is heard but no line covers it, using a more sensitive speech
          detector than transcription. It runs on the CPU and runs again after each transcription.
        </p>
        {active && status ? (
          <p className="actions" role="status" data-testid="speech-coverage-running">
            <span>{safeDetail(status.message) ?? 'Finding speech…'}</span>
            <button type="button" disabled={cancelSentFor === status} onClick={cancel}>Cancel</button>
          </p>
        ) : (
          <div className="actions">
            <button type="button" disabled={!!blocker || starting} onClick={start}>
              {report ? 'Check again' : 'Check coverage'}
            </button>
            {blocker && <span className="muted">{blocker}</span>}
          </div>
        )}
        <ErrorBanner error={error ?? loadError} onDismiss={() => { setError(null); clearError() }} />
        {status?.status === 'error' && (
          <p className="error" role="alert">
            {safeDetail(status.message) ?? 'The check failed. Details are in the app log.'}
          </p>
        )}
        {status?.status === 'cancelled' && <p className="muted">The check was cancelled.</p>}
        {report?.failed_reason && <p className="error" role="alert">{failedText(report)}</p>}
        {report && !report.failed_reason && (
          <>
            <p data-testid="coverage-totals">{coverageTotals(report)}</p>
            {report.gaps.length === 0 ? (
              <p className="muted" data-testid="coverage-none">No gaps of {report.min_gap_seconds} s or more.</p>
            ) : isPhone ? (
              <ul className="speech-coverage-cards" data-testid="coverage-gaps">
                {report.gaps.map((g) => (
                  <li key={`${g.start}-${g.end}`}>
                    <strong>{gapRange(g.start, g.end)}</strong> <span className="muted">{g.seconds.toFixed(1)} s</span>
                    <div>{rawNote(g)}</div>
                    {actions(g)}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="table-scroll">
                <table data-testid="coverage-gaps">
                  <thead>
                    <tr><th>Time</th><th>Length</th><th>Whisper's raw text</th><th /></tr>
                  </thead>
                  <tbody>
                    {report.gaps.map((g) => (
                      <tr key={`${g.start}-${g.end}`}>
                        <td>{gapRange(g.start, g.end)}</td>
                        <td>{g.seconds.toFixed(1)} s</td>
                        <td>{rawNote(g)}</td>
                        <td>{actions(g)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {report.gaps_total > report.gaps.length && (
              <p className="muted">Showing the first {report.gaps.length} of {report.gaps_total} gaps.</p>
            )}
            {!report.raw_available && report.gaps.length > 0 && (
              <p className="muted">This title has no saved raw transcript, so lost text can't be told from missed speech.</p>
            )}
          </>
        )}
        {report && hasAudio && (
          <audio
            ref={audioRef}
            controls
            preload="none"
            src={mediaStreamUrl(dramaId, 'audio')}
            data-testid="coverage-audio"
            onTimeUpdate={(e) => {
              const stop = stopAt.current
              if (stop !== null && e.currentTarget.currentTime >= stop) {
                stopAt.current = null
                e.currentTarget.pause()
              }
            }}
          />
        )}
      </div>
    </Section>
  )
}
