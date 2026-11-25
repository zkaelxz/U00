import { useEffect, useRef, useState } from 'react'

import { acceptGlossaryInbox, getGlossaryInbox, rejectGlossaryInbox, restoreGlossaryInbox } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import './glossaryVariants.css'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'
import type { GlossaryInbox as Inbox } from '../../../types/translateStage'
import { useStage } from '../StageContext'

// Names the app found by itself when a translation finished. Each waits for the person:
// Add needs the English spelling typed, Ignore keeps the name from coming back for this title.
export function GlossaryInbox({ onAdded }: { onAdded: () => void }) {
  const { dramaId } = useStage()
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [english, setEnglish] = useState<Record<string, string>>({})
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  // The last Ignore, takeable back for a few seconds (the same 8 s as the bookmarks Undo).
  const [ignored, setIgnored] = useState<{ term: string; spellings: string[] } | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (undoTimer.current) clearTimeout(undoTimer.current) }, [])

  useCancellableEffect((cancelled) => {
    getGlossaryInbox(dramaId).then((r) => !cancelled() && setInbox(r), () => undefined)
  }, [dramaId])

  const act = (call: Promise<Inbox>, added: boolean) => {
    setBusy(true)
    call.then((r) => { setInbox(r); setError(null); if (added) onAdded() }, setError).finally(() => setBusy(false))
  }

  const ignore = (term: string, spellings: string[]) => {
    setBusy(true)
    rejectGlossaryInbox(dramaId, term).then((r) => {
      setInbox(r)
      setError(null)
      setIgnored({ term, spellings })
      if (undoTimer.current) clearTimeout(undoTimer.current)
      undoTimer.current = setTimeout(() => setIgnored(null), 8000)
    }, setError).finally(() => setBusy(false))
  }
  const undoIgnore = () => {
    if (!ignored) return
    const { spellings } = ignored
    setIgnored(null)
    if (undoTimer.current) clearTimeout(undoTimer.current)
    setBusy(true)
    restoreGlossaryInbox(dramaId, spellings).then((r) => { setInbox(r); setError(null) }, setError).finally(() => setBusy(false))
  }

  if (!inbox || (inbox.count === 0 && !ignored)) return null
  return (
    <div className="glossary-variants" role="region" aria-label="Names waiting for you" data-testid="glossary-inbox">
      <h2 className="subhead">Names waiting for you ({inbox.count})</h2>
      <p className="muted">Nothing is added until you type the English and press Add.</p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {ignored && (
        <p role="status">
          Ignored {ignored.term}.{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={undoIgnore} disabled={busy}>Undo</button>
        </p>
      )}
      <ul className="glossary-variants-list">
        {inbox.items.map((c) => {
          const term = c.suggested_original
          const value = english[term] ?? ''
          return (
            <li key={term}>
              <strong>{term}</strong>
              {c.spellings.length > 1 && <span className="muted"> also written {c.spellings.filter((s) => s.text !== term).map((s) => s.text).join(', ')}</span>}
              <span className="muted"> · {c.total} mention{c.total === 1 ? '' : 's'}</span>
              {c.examples[0] && <div className="muted">{c.examples[0].snippet}</div>}
              <form className="glossary-inbox-row" onSubmit={(e) => { e.preventDefault(); if (value.trim() && !busy) act(acceptGlossaryInbox(dramaId, term, value.trim()), true) }}>
                <input
                  aria-label={`English for ${term}`}
                  value={value}
                  maxLength={200}
                  placeholder="English spelling"
                  autoCapitalize="words" autoCorrect="off" spellCheck={false} enterKeyHint="done"
                  onChange={(e) => setEnglish((cur) => ({ ...cur, [term]: e.target.value }))}
                />
                <button type="submit" className={buttonClass('secondary', 'sm')} disabled={busy || !value.trim()} aria-label={`Add ${term}`}>Add</button>
                <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy}
                  onClick={() => ignore(term, c.spellings.map((x) => x.text))} aria-label={`Ignore ${term}`}>Ignore</button>
              </form>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
