import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { getNovelFlow, startNovelFlow } from '../../api/queue'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { usePersistedState } from '../../hooks/usePersistedState'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { flowActive, flowText, useFlowRun } from './flowRun'
import { translateWithoutReview } from './novelLinkActions'

const STEP_TEXT: Record<string, string> = {
  import: 'Importing the chapters',
  text: 'Preparing the text',
  lines: 'Splitting into lines and tagging speakers',
  names: 'Finding names for the glossary',
}

/**
 * One box for a novel title: the link puts a run on the PC queue that imports, makes the lines and finds
 * glossary terms, then stops at the glossary review (the Translate tab offers it with a start button),
 * because names and genders are cheaper to fix before translating than after. The PC carries it on with
 * the page closed. With chapters already saved but no lines, the same button continues from where the title is.
 */
export function NovelLinkStart({ id, chapters, translating, defaultUrl, hasSeries, startSignal, onProgress }: {
  id: number
  hasSeries: boolean
  chapters: number
  translating: boolean
  defaultUrl: string
  /** Raised by the title page's Translate button, which has no lines to open and so runs the chain. */
  startSignal: number
  onProgress: () => void
}) {
  const [url, setUrl] = useState(defaultUrl)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // Off by default: the pause at the glossary review is the standing behaviour; this is per browser.
  const [skipReview, setSkipReview] = usePersistedState('novel.skipGlossaryReview', false)
  const [started, setStarted] = useState(false)
  // Skipping the review translates from this page, so only a run this page saw reach the review does it.
  const skip = useRef(skipReview)
  useEffect(() => { skip.current = skipReview })
  const { run, setRun, again } = useFlowRun(id, getNovelFlow, (r, ended) => {
    onProgress()
    if (ended && r.state === 'review' && skip.current) {
      translateWithoutReview(id, hasSeries).then(() => { setStarted(true); onProgress() }, setError)
    }
  })
  const running = starting || flowActive(run)
  const resuming = chapters > 0
  const finished = run?.state === 'review' && !started

  const start = () => {
    setStarting(true)
    setError(null)
    startNovelFlow(id, resuming ? '' : url.trim()).then((r) => { setRun(r); again() }, (e: unknown) => setError(e))
      .finally(() => setStarting(false))
  }

  // Not a dependency on `running`: a press while the run already works must not ask for a second one.
  useEffect(() => {
    if (startSignal > 0 && !running && resuming) start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startSignal])

  if (!running && !finished && translating) return null
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!running && (resuming || url.trim())) start()
  }
  const stopped = !!error || run?.state === 'stopped'
  return (
    <form className="panel" aria-label="Import from a link" aria-busy={running} onSubmit={submit}>
      {!resuming && !finished && (
        <input
          type="url"
          aria-label="Link to the novel"
          placeholder="Link to the novel"
          inputMode="url"
          autoComplete="url"
          autoCapitalize="off"
          spellCheck={false}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          readOnly={running}
        />
      )}
      <div className="actions">
        {finished ? (
          <ButtonLink variant="primary" href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'translate' })}>
            Review glossary and translate
          </ButtonLink>
        ) : (
          <button type="submit" className={buttonClass('primary')} disabled={running || (!resuming && !url.trim())}>
            {resuming || stopped ? 'Continue' : 'Import from link'}
          </button>
        )}
        <span className="muted" role="status" aria-live="polite">{started ? 'Translation started' : flowText(run, STEP_TEXT)}</span>
      </div>
      {!started && (
        <Field label="Skip the glossary review" help="After the glossary scan, add the proposed terms that have a translation and start translating without stopping to review them. Needs this page open when the scan ends.">
          <Toggle checked={skipReview} disabled={running} onChange={setSkipReview} />
        </Field>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
