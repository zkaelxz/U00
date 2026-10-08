import { useState } from 'react'

import { getRealModelCheck, startRealModelCheck } from '../../api/realModelCheck'
import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import type { RealModelCheckResult, RealModelCheckStatus } from '../../types/realModelCheck'
import { adminErrorText } from './diagnosticsAdmin'
import { jobProgressLine } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

const STATUS_TEXT: Record<RealModelCheckStatus, string> = {
  pass: 'Passed', fail: 'Failed', skipped: 'Skipped', could_not_check: 'Could not check',
}
const STATUS_TONE = { pass: 'ok', fail: 'bad', skipped: 'neutral', could_not_check: 'neutral' } as const

export function RealModelCheckResults({ checks }: { checks: RealModelCheckResult[] }) {
  return (
    <ul className="diag-rows diag-stack" data-testid="real-model-results">
      {checks.map((c) => (
        <li key={c.id} data-testid={`real-model-${c.id}`}>
          <strong>{c.label}</strong> <Badge tone={STATUS_TONE[c.status]}>{STATUS_TEXT[c.status]}</Badge>
          <br />
          <span className="muted">{c.reason}</span>
        </li>
      ))}
    </ul>
  )
}

/** "Real-model check": one transcription, OCR read and Ollama translation with the models you have. Complements "Test first", which only runs mocked tests. PC only. */
export function RealModelCheck({ pc, jobsActive }: { pc: PcMode; jobsActive: boolean }) {
  if (pc === 'remote') {
    return (
      <Section title="Real-model check" storageKey="diagnostics.realModel" summary={PC_ONLY_SUMMARY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return <RealModelCheckBody local={pc === 'local'} jobsActive={jobsActive} />
}

function RealModelCheckBody({ local, jobsActive }: { local: boolean; jobsActive: boolean }) {
  const { status, running, refresh } = useServerJobStatus(getRealModelCheck)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await startRealModelCheck()
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      setStarting(false)
      await refresh()
    }
  }
  const checks = status?.checks ?? []
  const blocked = running || starting || jobsActive
  return (
    <Section title="Real-model check" storageKey="diagnostics.realModel">
      <div className="diag-stack" data-testid="real-model-check">
        <p className="muted">
          Runs a short transcription, an OCR read and one Ollama translation with the models you have installed.
          Nothing is downloaded: a missing package, model or Ollama is skipped. A pass means the model loaded
          and ran, not that words were recognised. Qwen3-ASR is skipped ("Tone only") when the sample
          has no speech, because it then never loads. It uses the GPU, so close other GPU apps first.
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        {running && status?.job && (
          <>
            <progress max={1} value={status.job.progress || 0} aria-label="Real-model check progress" />
            <p className="muted" aria-live="polite">{jobProgressLine(status.job)}</p>
          </>
        )}
        {checks.length > 0 && <RealModelCheckResults checks={checks} />}
        {local && (
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={blocked}
              aria-busy={running || starting} onClick={() => void start()}>
              Run real-model check
            </button>
            {jobsActive && !running && <span className="muted">Wait for running jobs to finish.</span>}
          </div>
        )}
      </div>
    </Section>
  )
}
