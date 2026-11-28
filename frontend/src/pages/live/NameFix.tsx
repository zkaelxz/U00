/** Fix a misheard or mistranslated name for the rest of a running Live session. */
import { useState, type FormEvent } from 'react'

import { fixLiveName } from '../../api/live'
import { describeError } from '../../components/errorMessages'
import { buttonClass } from '../../components/uiClasses'
import type { LiveNameFix } from '../../types/live'

/** Text the reader has selected in a line's original, which is the name as heard. */
function selectedSource(): string {
  const sel = typeof window === 'undefined' ? null : window.getSelection()
  const node = sel?.anchorNode
  const el = node && (node.nodeType === 1 ? (node as Element) : node.parentElement)
  if (!sel || !el?.closest('.live-src')) return ''
  return sel.toString().trim().slice(0, 40)
}

type Props = {
  sessionId: string
  fixes: LiveNameFix[]
  hasGlossary: boolean
  lang: string
}

export function NameFix({ sessionId, fixes, hasGlossary, lang }: Props) {
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState('')
  const [english, setEnglish] = useState('')
  const [save, setSave] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<LiveNameFix[] | null>(null)
  const shown = saved && saved.length >= fixes.length ? saved : fixes

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await fixLiveName(sessionId, { source: source.trim(), english: english.trim(), save_to_glossary: hasGlossary && save })
      setSaved(r.fixes)
      setSource('')
      setEnglish('')
      setOpen(false)
    } catch (err) {
      const { title, detail } = describeError(err, { serverText: true })
      setError(detail ? `${title} ${detail}` : title)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="live-namefix" data-testid="live-namefix">
      {!open && (
        // Read on mouse down: by the click the selection in the line can already be gone.
        <button type="button" className={buttonClass('secondary')} onMouseDown={() => setSource(selectedSource() || source)} onClick={() => setOpen(true)}>
          Fix a name
        </button>
      )}
      {open && (
        <form onSubmit={(e) => void submit(e)} aria-label="Fix a name">
          <label>
            Name as heard
            <input lang={lang} value={source} maxLength={40} required onChange={(e) => setSource(e.target.value)} autoFocus={!source} />
          </label>
          <label>
            Right name in English
            <input value={english} maxLength={80} required onChange={(e) => setEnglish(e.target.value)} autoFocus={!!source} />
          </label>
          {hasGlossary && (
            <label className="live-namefix-save">
              <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
              Also save it to the glossary
            </label>
          )}
          <div className="live-namefix-actions">
            <button type="submit" className={buttonClass('primary')} disabled={busy || !source.trim() || !english.trim()} aria-busy={busy || undefined}>Fix</button>
            <button type="button" className={buttonClass('secondary')} onClick={() => { setOpen(false); setError(null) }}>Cancel</button>
          </div>
          <p className="muted">New lines use it from now on. Lines already shown stay as they are.</p>
        </form>
      )}
      {error && <p className="warn" role="alert">{error}</p>}
      {shown.length > 0 && (
        <p className="muted" data-testid="live-namefix-list">
          Fixed: {shown.map((f, i) => <span key={f.source}>{i ? ', ' : ''}<span lang={lang}>{f.source}</span> → {f.english}</span>)}
        </p>
      )}
    </div>
  )
}
