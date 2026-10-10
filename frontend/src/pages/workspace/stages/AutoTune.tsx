import { useEffect, useRef, useState } from 'react'

import { applyAutotune, getAutotune, startAutotune } from '../../../api/autotuneGlossary'
import { ApiError } from '../../../api/client'
import { cancelJob } from '../../../api/jobs'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Section } from '../../../components/Section'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { AutotuneCandidateScore } from '../../../types/autotuneGlossary'
import type { TranscribeConfig } from '../../../types/workspace'
import { useStage } from '../StageContext'
import { promptFields } from './transcribePrompt'
import {
  autotuneApplyErrorText,
  autotuneBlocker,
  autotuneEta,
  formatElapsed,
  autotuneProgressText,
  isActiveStatus,
} from './autotuneGlossary'
import { useRunStatus } from './useRunStatus'
import './autotuneGlossary.css'
import { buttonClass } from '../../../components/uiClasses'

interface Props {
  hasAudio: boolean
  // Another job for this drama (transcribe, diarize, upload) is running.
  busy: boolean
  // The Transcribe prompt inputs, used for every test run: a replacement prompt
  // (wins when set) or extra names added to the automatic prompt.
  override: string
  extraNames: string
  onApplied: (config: TranscribeConfig) => void
}

const BEST = 'Fewest long lines'

// Transcribe → Advanced → "Auto-tune min silence": transcribes the audio once
// per candidate and scores how many long lines each gives (local ASR only).
export function AutoTune({ hasAudio, busy, override, extraNames, onApplied }: Props) {
  const { dramaId } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, refresh, clearError } = useRunStatus(dramaId, getAutotune)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const active = isActiveStatus(status?.status)
  // Seconds since this panel saw the run start; a reattached run counts from
  // when it was found, so the clock is a lower bound.
  const [elapsed, setElapsed] = useState(0)
  // The estimate needs the true start: false when the run was already past its
  // first candidate when this panel found it.
  const [fromStart, setFromStart] = useState(true)
  const firstMessage = useRef('')
  firstMessage.current = status?.message ?? ''
  useEffect(() => {
    if (!active) return
    const begun = Date.now()
    setElapsed(0)
    setFromStart(!/(\d+)\s+of\s+\d+/i.test(firstMessage.current) || /\b1\s+of\b/i.test(firstMessage.current))
    const t = setInterval(() => setElapsed((Date.now() - begun) / 1000), 1000)
    return () => clearInterval(t)
  }, [active])
  const blocker = active ? null : autotuneBlocker(hasAudio, busy)
  const results = status?.status === 'done' ? status.results ?? [] : []
  const best = status?.best_candidate_ms ?? null

  const start = () => {
    setStarting(true)
    setError(null)
    setProblem(null)
    setNote(null)
    startAutotune(dramaId, promptFields(override, extraNames))
      .then(
        () => refresh(),
        (e: unknown) => {
          // 409: a run is already going (another tab); just attach to it.
          if (e instanceof ApiError && e.status === 409) refresh()
          else setError(e)
        },
      )
      .finally(() => setStarting(false))
  }

  // Cancel stays disabled after a press until the next poll brings new status.
  const [cancelSentFor, setCancelSentFor] = useState<object | null>(null)
  const cancel = () => {
    if (!status) return
    setCancelSentFor(status)
    cancelJob(status.job_id).then(refresh, (e: unknown) => {
      setCancelSentFor(null)
      setError(e)
    })
  }

  const use = (ms: number) => {
    setProblem(null)
    applyAutotune(dramaId, ms).then(
      (c) => {
        setError(null)
        setNote(`Min silence set to ${ms} ms. Transcribe again to apply.`)
        onApplied(c)
      },
      (e: unknown) => {
        const text = autotuneApplyErrorText(e)
        if (text) setProblem(text)
        else setError(e)
      },
    )
  }

  const summary = active
    ? 'running'
    : status?.status === 'done' && best !== null
      ? `best ${best} ms`
      : 'finds the value with the fewest long lines'

  const applyButton = (r: AutotuneCandidateScore) => (
    <button type="button" onClick={() => use(r.candidate_ms)}>
      Use {r.candidate_ms} ms
    </button>
  )

  return (
    <Section storageKey="source.autotune" title="Auto-tune min silence" summary={summary}>
      <div className="autotune" data-testid="autotune">
        <p className="muted">
          Transcribes the audio once per test value and counts the long lines each gives. Uses
          the GPU and can take a while. It uses the same Whisper prompt as Transcribe.
        </p>
        {active && status ? (
          <p className="actions" role="status" data-testid="autotune-running">
            <span>
              {autotuneProgressText(status.status, status.message)}{' '}
              <span className="muted" data-testid="autotune-elapsed">
                {formatElapsed(elapsed)} elapsed
                {status.status === 'running' && fromStart && autotuneEta(elapsed, status.message) && ` · ${autotuneEta(elapsed, status.message)}`}
              </span>
            </span>
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={cancelSentFor === status} onClick={cancel}>
              Cancel
            </button>
          </p>
        ) : (
          <div className="actions">
            <button type="button" disabled={!!blocker || starting} onClick={start}>
              {status?.status === 'done' ? 'Run auto-tune again' : 'Start auto-tune'}
            </button>
            {blocker && <span className="muted">{blocker}</span>}
          </div>
        )}
        {status?.status === 'error' && (
          <p className="error" role="alert">
            {safeDetail(status.message) ?? 'Auto-tune failed. Details are in the app log.'}
          </p>
        )}
        {status?.status === 'cancelled' && <p className="muted">Auto-tune was cancelled.</p>}
        {results.length > 0 &&
          (isPhone ? (
            <ul className="autotune-cards" data-testid="autotune-results">
              {results.map((r) => (
                <li key={r.candidate_ms}>
                  <strong>{r.candidate_ms} ms</strong>
                  {r.candidate_ms === best && <>{' '}<span className="badge ok">{BEST}</span></>}
                  <div className="muted">
                    {r.long_lines} long lines · {r.total_lines} lines
                  </div>
                  <div className="autotune-card-action">{applyButton(r)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="table-scroll">
              <table data-testid="autotune-results">
                <thead>
                  <tr>
                    <th>Min silence</th>
                    <th>Long lines</th>
                    <th>Total lines</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => (
                    <tr key={r.candidate_ms}>
                      <td>
                        {r.candidate_ms} ms
                        {r.candidate_ms === best && <>{' '}<span className="badge ok">{BEST}</span></>}
                      </td>
                      <td>{r.long_lines}</td>
                      <td>{r.total_lines}</td>
                      <td>{applyButton(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        {note && <p role="status">{note}</p>}
        {problem && <p className="error" role="alert">{problem}</p>}
        <ErrorBanner
          error={error ?? loadError}
          onDismiss={() => {
            setError(null)
            clearError()
          }}
        />
      </div>
    </Section>
  )
}
