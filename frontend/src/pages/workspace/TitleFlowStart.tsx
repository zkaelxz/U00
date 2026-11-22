import { useState } from 'react'

import { getTitleFlow, startTitleFlow } from '../../api/queue'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { flowActive, flowText, stoppedJob, useFlowRun } from './flowRun'

const STEP_TEXT: Record<string, string> = {
  transcribe: 'Transcribing',
  names: 'Finding names for the glossary',
}

/**
 * The Simple page's main button for an audio or video title with audio but no lines: it puts a run on
 * the PC queue that transcribes, finds glossary names, then stops at the glossary review, because names
 * are cheaper to fix before translating. The PC carries it on with the page closed; this only shows it.
 */
export function TitleFlowStart({ id, onProgress }: { id: number; onProgress: () => void }) {
  const { run, setRun, again } = useFlowRun(id, getTitleFlow, () => onProgress())
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const job = stoppedJob(run)

  const start = () => {
    setStarting(true)
    setError(null)
    startTitleFlow(id).then((r) => { setRun(r); again() }, (e: unknown) => setError(e)).finally(() => setStarting(false))
  }

  return (
    <>
      {run?.state === 'review' ? (
        <ButtonLink variant="primary" href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Review the glossary</ButtonLink>
      ) : (
        <button type="button" className={buttonClass('primary')} disabled={starting || flowActive(run)} onClick={start}>
          {/* Continue goes on from the stopped step: the server adds only the steps the title still needs. A refused
              start is not a stopped run, so pressing again would only be refused again; the banner says why. */}
          {!error && run?.state === 'stopped' ? 'Continue' : 'Transcribe and build glossary'}
        </button>
      )}
      <span className="muted" role="status" aria-live="polite">{flowText(run, STEP_TEXT)}</span>
      {job && <a href={routeHref({ name: 'jobs', job })}>See details</a>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}
