import { useState } from 'react'
import type { FormEvent } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { ChainStall, runChain, type ChainStep } from './novelLinkChain'
import { novelLinkActions } from './novelLinkActions'

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
 * the glossary review, because names and genders are cheaper to fix before translating than after.
 * With chapters already saved but no lines, the same button continues from where the title is.
 */
export function NovelLinkStart({ id, chapters, translating, defaultUrl, onProgress }: {
  id: number
  chapters: number
  translating: boolean
  defaultUrl: string
  onProgress: () => void
}) {
  const [url, setUrl] = useState(defaultUrl)
  const [step, setStep] = useState<ChainStep | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const resuming = chapters > 0
  const finished = step === 'review'

  const start = () => {
    setRunning(true)
    setError(null)
    runChain(novelLinkActions(id, url.trim()), (s) => { setStep(s); onProgress() }).then(
      () => setRunning(false),
      (e: unknown) => {
        setRunning(false)
        setError(e instanceof ChainStall ? new Error(`${STEP_TEXT[e.message as ChainStep] ?? 'The step'} did not change the title.`) : e)
      },
    )
  }

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
          <ButtonLink variant="primary" href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>
            Review the glossary
          </ButtonLink>
        ) : (
          <button type="submit" className={buttonClass('primary')} disabled={running || (!resuming && !url.trim())}>
            {resuming || error ? 'Continue' : 'Import from link'}
          </button>
        )}
        <span className="muted" role="status" aria-live="polite">{step ? STEP_TEXT[step] : ''}</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
