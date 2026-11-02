import { useState } from 'react'

import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { titleFlowActions } from './titleFlowActions'
import { TitleFlowStall, runTitleFlow, type TitleFlowStep } from './titleFlowChain'

const STEP_TEXT: Record<TitleFlowStep, string> = {
  transcribe: 'Transcribing',
  extract: 'Finding names for the glossary',
  wait: 'Finding names for the glossary',
  review: 'Ready for glossary review',
}

/**
 * The Simple page's main button for an audio or video title with audio but no lines: it transcribes, finds
 * glossary names, then stops at the glossary review, because names are cheaper to fix before translating.
 */
export function TitleFlowStart({ id, onProgress }: { id: number; onProgress: () => void }) {
  const [step, setStep] = useState<TitleFlowStep | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const start = () => {
    setRunning(true)
    setError(null)
    runTitleFlow(titleFlowActions(id), (s) => { setStep(s); onProgress() }).then(
      () => setRunning(false),
      (e: unknown) => {
        setRunning(false)
        setError(e instanceof TitleFlowStall ? new Error(`${STEP_TEXT[e.message as TitleFlowStep] ?? 'The step'} did not change the title.`) : e)
      },
    )
  }

  return (
    <>
      {step === 'review' ? (
        <ButtonLink variant="primary" href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Review the glossary</ButtonLink>
      ) : (
        <button type="button" className={buttonClass('primary')} disabled={running} onClick={start}>
          {error ? 'Continue' : 'Transcribe and translate'}
        </button>
      )}
      <span className="muted" role="status" aria-live="polite">{step ? STEP_TEXT[step] : ''}</span>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}
