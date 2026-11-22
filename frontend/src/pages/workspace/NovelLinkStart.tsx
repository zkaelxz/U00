import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { narrationApi } from '../../api/dub'
import { getNovelFlow, startNovelFlow } from '../../api/queue'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { usePersistedState } from '../../hooks/usePersistedState'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { flowActive, flowText, stoppedJob, useFlowRun } from './flowRun'
import { translateWithoutReview } from './novelLinkActions'
import { TagChoices } from './stages/TagChoices'
import { EMPTY_TAG_CHOICE, tagChoiceProblem, tagRunBody, type TagChoice } from './stages/tagChoice'
import type { NarrationConfig } from '../../types/dub'

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
  // The AI engine, model and "one narrator" are remembered per browser; the chapters are for this run only.
  const [saved, setSaved] = usePersistedState('novel.tagChoice', { engine: '', model: '', oneNarrator: false })
  const [range, setRange] = useState({ from: '', to: '' })
  const tag: TagChoice = { ...EMPTY_TAG_CHOICE, ...saved, ...range }
  const tagNow = useRef(tag)
  useEffect(() => { tagNow.current = tag })
  const [cfg, setCfg] = useState<NarrationConfig | null>(null)
  const [blocked, setBlocked] = useState<string | null>(null)
  const costAccepted = useRef(false)
  const onAccepted = useCallback((v: boolean) => { costAccepted.current = v }, [])
  // Skipping the review translates from this page, so only a run this page saw reach the review does it.
  const skip = useRef(skipReview)
  useEffect(() => { skip.current = skipReview })
  const { run, setRun, again } = useFlowRun(id, getNovelFlow, (r, ended) => {
    onProgress()
    if (ended && r.state === 'review' && skip.current) {
      translateWithoutReview(id, hasSeries, tagNow.current).then(() => { setStarted(true); onProgress() }, setError)
    }
  })
  const running = starting || flowActive(run)
  const resuming = chapters > 0
  const finished = run?.state === 'review' && !started

  // Read again as the run moves on: the text only exists once it is prepared, and the estimate needs it.
  useEffect(() => {
    if (!resuming) return
    let cancelled = false
    narrationApi.config(id).then((c) => !cancelled && setCfg(c), () => undefined)
    return () => { cancelled = true }
  }, [id, resuming, run?.state, run?.step])

  const start = () => {
    const problem = tagChoiceProblem(tag)
    if (problem) {
      setError(new Error(problem))
      return
    }
    setStarting(true)
    setError(null)
    // Before the text exists there is no choice to show, and the server's default engine makes the lines.
    const { engine, ...choice } = tagRunBody(tag, cfg?.default_engine ?? '')
    const lines = { ...choice, ...(engine ? { engine } : {}), cost_accepted: costAccepted.current }
    startNovelFlow(id, resuming ? '' : url.trim(), lines).then((r) => { setRun(r); again() }, (e: unknown) => setError(e))
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
  // A refused start is not a stopped run: pressing again would only be refused again, and the banner says why.
  const stopped = !error && run?.state === 'stopped'
  const job = stoppedJob(run)
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
          <button type="submit" className={buttonClass('primary')} disabled={running || (!resuming && !url.trim()) || !!blocked}>
            {resuming || stopped ? 'Continue' : 'Import from link'}
          </button>
        )}
        <span className="muted" role="status" aria-live="polite">{started ? 'Translation started' : flowText(run, STEP_TEXT)}</span>
        {!started && job && <a href={routeHref({ name: 'jobs', job })}>See details</a>}
      </div>
      {resuming && !finished && !started && cfg && (
        <TagChoices
          dramaId={id}
          cfg={cfg}
          value={tag}
          disabled={running}
          onChange={(next) => {
            setSaved({ engine: next.engine, model: next.model, oneNarrator: next.oneNarrator })
            setRange({ from: next.from, to: next.to })
          }}
          onBlocked={setBlocked}
          onAccepted={onAccepted}
        />
      )}
      {!started && (
        <Field label="Skip the glossary review" help="After the glossary scan, add the proposed terms that have a translation and start translating without stopping to review them. Needs this page open when the scan ends.">
          <Toggle checked={skipReview} disabled={running} onChange={setSkipReview} />
        </Field>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
