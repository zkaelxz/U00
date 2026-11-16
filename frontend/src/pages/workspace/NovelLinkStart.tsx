import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { narrationApi } from '../../api/dub'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { usePersistedState } from '../../hooks/usePersistedState'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { ChainStall, runChain, type ChainStep } from './novelLinkChain'
import { novelLinkActions, translateWithoutReview } from './novelLinkActions'
import { TagChoices } from './stages/TagChoices'
import { EMPTY_TAG_CHOICE, type TagChoice } from './stages/tagChoice'
import type { NarrationConfig } from '../../types/dub'

const STEP_TEXT: Record<ChainStep, string> = {
  import: 'Importing the chapters',
  attach: 'Preparing the text',
  tag: 'Splitting into lines and tagging speakers',
  extract: 'Finding names for the glossary',
  wait: 'Finding names for the glossary',
  review: 'Ready for glossary review',
}

/**
 * One box for a novel title: the link runs import, lines and glossary extraction in turn and stops at
 * the glossary review (the Translate tab offers it with a start button), because names and genders are cheaper to fix before translating than after.
 * With chapters already saved but no lines, the same button continues from where the title is.
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
  const [step, setStep] = useState<ChainStep | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // Off by default: the pause at the glossary review is the standing behaviour; this is per browser.
  const [skipReview, setSkipReview] = usePersistedState('novel.skipGlossaryReview', false)
  const [started, setStarted] = useState(false)
  // The AI engine, model and "one narrator" are remembered per browser; the chapters are for this run only.
  const [saved, setSaved] = usePersistedState('novel.tagChoice', { engine: '', model: '', oneNarrator: false })
  const [range, setRange] = useState({ from: '', to: '' })
  const tag: TagChoice = { ...EMPTY_TAG_CHOICE, ...saved, ...range }
  const [cfg, setCfg] = useState<NarrationConfig | null>(null)
  const [blocked, setBlocked] = useState<string | null>(null)
  const costAccepted = useRef(false)
  const onAccepted = useCallback((v: boolean) => { costAccepted.current = v }, [])
  const resuming = chapters > 0
  const finished = step === 'review' && !started

  // Read again after each step: the text only exists once it is prepared, and the estimate needs it.
  useEffect(() => {
    if (!resuming) return
    let cancelled = false
    narrationApi.config(id).then((c) => !cancelled && setCfg(c), () => undefined)
    return () => { cancelled = true }
  }, [id, resuming, step])

  const start = () => {
    setRunning(true)
    setError(null)
    const run = async () => {
      await runChain(novelLinkActions(id, url.trim(), tag, () => costAccepted.current), (s) => { setStep(s); onProgress() })
      if (skipReview) {
        await translateWithoutReview(id, hasSeries, tag)
        setStarted(true)
        onProgress()
      }
    }
    run().then(
      () => setRunning(false),
      (e: unknown) => {
        setRunning(false)
        setError(e instanceof ChainStall ? new Error(`${STEP_TEXT[e.message as ChainStep] ?? 'The step'} did not change the title.`) : e)
      },
    )
  }

  // Not a dependency on `running`: a press while the chain already works must not queue a second run.
  useEffect(() => {
    if (startSignal > 0 && !running && resuming) start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startSignal])

  if (!running && !finished && translating) return null
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!running && (resuming || url.trim())) start()
  }
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
            {resuming || error ? 'Continue' : 'Import from link'}
          </button>
        )}
        <span className="muted" role="status" aria-live="polite">{started ? 'Translation started' : step ? STEP_TEXT[step] : ''}</span>
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
        <Field label="Skip the glossary review" help="After the glossary scan, add the proposed terms that have a translation and start translating without stopping to review them.">
          <Toggle checked={skipReview} disabled={running} onChange={setSkipReview} />
        </Field>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
