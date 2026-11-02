import { useCallback, useId, useState } from 'react'

import { cancelJob } from '../../api/jobs'
import { getVoiceEngines, installVoiceEngine, removeVoiceEngine } from '../../api/voiceEngines'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { adminErrorText, installBlockedReason, type AdminBusy } from './diagnosticsAdmin'
import { jobProgressLine } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'
import {
  VOICE_ENGINE_CARDS, VOICE_ENGINE_INTRO, canOfferVoiceEngine, canRemoveVoiceEngine, voiceEngineDiskLine,
  voiceEngineRemoveNote, voiceEngineResultLine, voiceEngineStatusLine, type VoiceEngineKey,
} from './voiceEngineText'

/**
 * Packages and installs card: "Voice cloning: <engine>" (PC only, two-step). The server
 * installs it into a folder of its own as a job; this polls its status, offers Cancel while
 * it runs, then shows the result.
 */
export function VoiceEngineInstall({ engine, pc, jobsActive, busy, onStarted, onFinished, openSignal }: {
  engine: VoiceEngineKey
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onStarted: () => void
  onFinished: () => void
  openSignal?: number
}) {
  const load = useCallback(() => getVoiceEngines().then((s) => {
    const row = s.engines.find((e) => e.engine === engine)
    if (!row) throw new Error('missing engine row')
    return row
  }), [engine])
  const { status, running, refresh } = useServerJobStatus(load, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const reasonId = useId()
  const pending = usePcPendingNote(pc)
  if (!status) return null

  const local = pc === 'local'
  const blocked = installBlockedReason(jobsActive, busy)
  const line = voiceEngineStatusLine(status)
  const result = voiceEngineResultLine(status)
  const tail = status.last_result?.output_tail ?? []
  const title = `Voice cloning: ${status.label}`
  const { card, gb, note } = VOICE_ENGINE_CARDS[engine]
  const run = async (action: (engine: string) => Promise<unknown>) => {
    setStarting(true)
    setError(null)
    try {
      await action(engine)
      onStarted()
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      setStarting(false)
      await refresh()
    }
  }
  const cancel = async () => {
    setError(null)
    try {
      await cancelJob(status.job_id)
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      await refresh()
    }
  }

  return (
    <Section title={title} storageKey={`diagnostics.${card}`} summary={line ?? 'Not installed'} openSignal={openSignal}>
    <div className="diag-subcard" data-testid={`${card}-install`} role="group" aria-label={title}>
      <p className="muted">{VOICE_ENGINE_INTRO}</p>
      {note && <p className="muted">{note}</p>}
      {!status.installed && <p className="muted">{voiceEngineDiskLine(status.label, gb)}</p>}
      {line && <p className="muted" data-testid={`${card}-status`}>{line}</p>}
      {running && status.job && (
        <div className="diag-stack" data-testid={`${card}-progress`}>
          <progress max={1} value={status.job.progress || 0} aria-label={`${status.label} install progress`} />
          <p className="muted" aria-live="polite">{jobProgressLine(status.job)}</p>
          {local && (
            <div className="actions">
              <button type="button" className={buttonClass('secondary')} onClick={() => void cancel()}>
                Cancel install
              </button>
            </div>
          )}
        </div>
      )}
      {result && (
        <p className={result.tone === 'ok' ? undefined : 'error'} role={result.tone === 'error' ? 'alert' : 'status'}
          data-testid={`${card}-result`}>
          {result.text}
        </p>
      )}
      {result?.tone === 'error' && tail.length > 0 && (
        <details>
          <summary>Installer output</summary>
          <pre className="diag-pre">{tail.join('\n')}</pre>
        </details>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {pending && canOfferVoiceEngine(status) && <p className="muted">{pending}</p>}
      {local && canOfferVoiceEngine(status) && (
        <div className="actions">
          <ConfirmButton
            name={status.label}
            label={`Install ${status.label}…`}
            ariaLabel={`Install ${status.label}`}
            verb="install"
            tone="primary"
            confirmLabel={`Confirm install ${status.label} (about ${gb} GB)`}
            disabled={!!blocked}
            describedBy={blocked ? reasonId : undefined}
            busy={starting}
            onConfirm={() => void run(installVoiceEngine)}
          />
          {blocked && <span className="muted" id={reasonId}>{blocked}</span>}
        </div>
      )}
      {local && canRemoveVoiceEngine(status) && (
        <div className="diag-stack" data-testid={`${card}-remove`}>
          <p className="muted">{voiceEngineRemoveNote(status.label, gb)}</p>
          <div className="actions">
            <ConfirmButton
              name={status.label}
              label={`Remove ${status.label}…`}
              ariaLabel={`Remove ${status.label}`}
              verb="remove"
              tone="danger"
              confirmLabel={`Confirm remove ${status.label}`}
              disabled={!!blocked}
              describedBy={blocked ? reasonId : undefined}
              busy={starting}
              onConfirm={() => void run(removeVoiceEngine)}
            />
          </div>
        </div>
      )}
    </div>
    </Section>
  )
}
