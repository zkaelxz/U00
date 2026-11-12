import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { deleteWordReplacement, getWordReplacements, saveWordReplacement } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import type { WordReplacement } from '../../../types/translateStage'
import { useStage } from '../StageContext'

// Whole-word swaps the series applies to every line a translation writes, for wording a model keeps
// choosing that the person wants gone. Lines already translated are changed in Review's find and replace.
export function WordReplacementsTab() {
  const { dramaId } = useStage()
  const [rows, setRows] = useState<WordReplacement[] | null>(null)
  const [find, setFind] = useState('')
  const [replace, setReplace] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    getWordReplacements(dramaId).then((r) => { setError(null); setRows(r) }, setError)
  }, [dramaId])
  useEffect(load, [load])
  const run = (work: Promise<unknown>) => {
    setBusy(true)
    work.then(() => { setError(null); load() }, setError).finally(() => setBusy(false))
  }
  const add = (e: FormEvent) => {
    e.preventDefault()
    run(saveWordReplacement(dramaId, { find_text: find, replace_text: replace }).then(() => { setFind(''); setReplace('') }))
  }
  return (
    <>
      <p className="muted">
        Each word below is swapped, whole words only and ignoring case, in every line a translation run writes for this series.
        Lines already translated are not changed.
      </p>
      <ErrorBanner error={error} />
      <form className="actions" onSubmit={add} aria-label="Add a word replacement">
        <Field label="Replace"><input value={find} maxLength={200} onChange={(e) => setFind(e.target.value)} /></Field>
        <Field label="With"><input value={replace} maxLength={200} onChange={(e) => setReplace(e.target.value)} /></Field>
        <button type="submit" className={buttonClass()} disabled={busy || !find.trim()}>Add</button>
      </form>
      {rows === null ? <p className="muted" role="status">Loading…</p> : rows.length === 0 ? <p className="muted">No word replacements yet.</p> : (
        <ul className="stack" aria-label="Word replacements">
          {rows.map((r) => (
            <li key={r.id}>
              <strong>{r.find_text}</strong> → {r.replace_text || <em>(nothing)</em>}{' '}
              <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy}
                aria-label={`Delete the replacement for ${r.find_text}`} onClick={() => run(deleteWordReplacement(dramaId, r.id))}>Delete</button>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
