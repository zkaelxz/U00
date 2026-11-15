import { useEffect, useRef, useState } from 'react'

import { getTitleFlow, startTitleFlow } from '../../api/queue'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import type { TitleFlowRun } from '../../types/queue'

const POLL_MS = 3000

const STEP_TEXT: Record<string, string> = {
  transcribe: 'Transcribing',
  names: 'Finding names for the glossary',
}

export const flowActive = (run: TitleFlowRun | null) => run?.state === 'waiting' || run?.state === 'running'

/** The words beside the button for the run as the PC holds it. */
export function flowText(run: TitleFlowRun | null): string {
  if (!run) return ''
  switch (run.state) {
    case 'waiting': return run.reason || 'Waiting for the item running in the queue'
    case 'running': return STEP_TEXT[run.step ?? ''] ?? 'Working'
    case 'review': return 'Ready for glossary review'
    case 'stopped': return `Stopped: ${run.reason || 'a step did not finish.'}`
    default: return ''
  }
}

/**
 * The Simple page's main button for an audio or video title with audio but no lines: it puts a run on
 * the PC queue that transcribes, finds glossary names, then stops at the glossary review, because names
 * are cheaper to fix before translating. The PC carries it on with the page closed; this only shows it.
 */
export function TitleFlowStart({ id, onProgress }: { id: number; onProgress: () => void }) {
  const [run, setRun] = useState<TitleFlowRun | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // Raised by a start, so the reads begin again after a run that had ended.
  const [reads, setReads] = useState(0)
  const seen = useRef('')
  const progress = useRef(onProgress)
  useEffect(() => { progress.current = onProgress })

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const read = () => {
      getTitleFlow(id).then((r) => {
        if (cancelled) return
        setRun(r)
        const key = `${r.state}:${r.step ?? ''}`
        const wasActive = /^(waiting|running):/.test(seen.current)
        // Each step that moves on refreshes the title's counts; a run found already ended leaves them be.
        if (key !== seen.current && (flowActive(r) || wasActive)) progress.current()
        seen.current = key
        if (flowActive(r)) timer = setTimeout(read, POLL_MS)
      }, () => {
        if (!cancelled) timer = setTimeout(read, POLL_MS * 2)
      })
    }
    read()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [id, reads])

  const start = () => {
    setStarting(true)
    setError(null)
    startTitleFlow(id).then(
      (r) => { setRun(r); setReads((n) => n + 1) },
      (e: unknown) => setError(e),
    ).finally(() => setStarting(false))
  }

  return (
    <>
      {run?.state === 'review' ? (
        <ButtonLink variant="primary" href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Review the glossary</ButtonLink>
      ) : (
        <button type="button" className={buttonClass('primary')} disabled={starting || flowActive(run)} onClick={start}>
          {error || run?.state === 'stopped' ? 'Continue' : 'Transcribe and translate'}
        </button>
      )}
      <span className="muted" role="status" aria-live="polite">{flowText(run)}</span>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}
