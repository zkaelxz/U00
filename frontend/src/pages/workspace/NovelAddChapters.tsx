import { useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { addChaptersFromLink, type AddStep } from './novelLinkActions'

const STEP_TEXT: Record<AddStep, string> = {
  import: 'Importing the chapters',
  text: 'Adding them to the translation text',
  lines: 'Cutting the new lines and tagging their speakers',
}

/**
 * The link box once translation has started: it only adds new chapters. Saved lines keep their
 * translation and speaker, and the new chapters become new lines after them; nothing is translated until Translate is pressed.
 */
export function NovelAddChapters({ id, chapters, defaultUrl, onProgress }: { id: number; chapters: number; defaultUrl: string; onProgress: () => void }) {
  const [url, setUrl] = useState(defaultUrl)
  const [step, setStep] = useState<AddStep | null>(null)
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  // The saved chapter count before the first attempt: a retry after a failed step still expects the same translation text.
  const baseline = useRef<number | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (running || !url.trim()) return
    setRunning(true)
    setError(null)
    setDone(null)
    baseline.current ??= chapters
    addChaptersFromLink(id, url.trim(), (s) => { setStep(s); onProgress() }, baseline.current).then(
      (added) => {
        baseline.current = null
        setRunning(false)
        setStep(null)
        setDone(added > 0
          ? 'Chapters added. Your translations and speakers are kept; press Translate to translate the new lines.'
          : 'Nothing new was added: those chapters are already in the translation text.')
        onProgress()
      },
      (e: unknown) => {
        setRunning(false)
        setStep(null)
        setError(e)
        onProgress()
      },
    )
  }

  return (
    <form className="panel" aria-label="Add chapters from a link" aria-busy={running} onSubmit={submit}>
      <input
        type="url"
        aria-label="Link to the next chapter"
        placeholder="Link to the next chapter"
        inputMode="url"
        autoComplete="url"
        autoCapitalize="off"
        spellCheck={false}
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        readOnly={running}
      />
      <div className="actions">
        <button type="submit" className={buttonClass('secondary')} disabled={running || !url.trim()}>Add from link</button>
        <span className="muted" role="status" aria-live="polite">{step ? STEP_TEXT[step] : (done ?? '')}</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
