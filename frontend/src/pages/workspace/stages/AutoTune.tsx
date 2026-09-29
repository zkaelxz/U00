import { useState } from 'react'

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
import {
  autotuneApplyErrorText,
  autotuneBlocker,
  autotuneProgressText,
  isActiveStatus,
} from './autotuneGlossary'
import { useRunStatus } from './useRunStatus'
import './autotuneGlossary.css'

interface Props {
  hasAudio: boolean
  // Another job for this drama (transcribe, diarize, upload) is running.
  busy: boolean
  // The Advanced "Initial prompt", used for every test run.
  prompt: string
  onApplied: (config: TranscribeConfig) => void
}

const BEST = 'Fewest long lines'

// Transcribe → Advanced → "Auto-tune min silence": transcribes the audio once
// per candidate and scores how many long lines each gives (local ASR only).
export function AutoTune({ hasAudio, busy, prompt, onApplied }: Props) {
  const { dramaId } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, refresh, clearError } = useRunStatus(dramaId, getAutotune)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const active = isActiveStatus(status?.status)
  const blocker = active ? null : autotuneBlocker(hasAudio, busy)
  const results = status?.status === 'done' ? status.results ?? [] : []
  const best = status?.best_candidate_ms ?? null

  const start = () => {
    setStarting(true)
    setError(null)
    setProblem(null)
    setNote(null)
    startAutotune(dramaId, prompt.trim() ? { initial_prompt: prompt } : {})
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
          the GPU and can take a while. It uses the Initial prompt above.
        </p>
        {active && status ? (
          <p className="actions" role="status" data-testid="autotune-running">
            <span>{autotuneProgressText(status.status, status.message)}</span>
            <button type="button" disabled={cancelSentFor === status} onClick={cancel}>
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
