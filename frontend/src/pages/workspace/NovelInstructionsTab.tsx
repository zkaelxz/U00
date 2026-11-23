import { useEffect, useState } from 'react'

import { draftInstructions, getInstructions, saveInstructions } from '../../api/translateStage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { joinConventions, splitConventions } from './novelConventions'

export function InstructionsForm({ id, saved, onSaved }: { id: number; saved: string; onSaved: (text: string) => void }) {
  const start = splitConventions(saved)
  const [prose, setProse] = useState(start.prose)
  const [conventions, setConventions] = useState(start.conventions)
  const [done, setDone] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const edit = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    setDone(false)
    set(e.target.value)
  }
  return (
    <div className="stack">
      <Field label="Instructions">
        <textarea rows={5} value={prose} onChange={edit(setProse)} />
      </Field>
      <Field label="Term conventions">
        <textarea rows={4} value={conventions} onChange={edit(setConventions)} />
      </Field>
      <div className="actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          onClick={() => {
            const text = joinConventions(prose, conventions)
            saveInstructions(id, 'project', text).then(() => {
              setError(null)
              setDone(true)
              onSaved(text)
            }, setError)
          }}
        >
          Save
        </button>
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={drafting}
          onClick={() => {
            setDrafting(true)
            // The draft only fills the box: nothing is saved until the person edits and presses Save.
            draftInstructions(id)
              .then((d) => {
                setError(null)
                setDone(false)
                setProse(d.instructions)
              }, setError)
              .finally(() => setDrafting(false))
          }}
        >
          {drafting ? 'Drafting…' : 'Draft from novel'}
        </button>
        {done && <span role="status">Saved.</span>}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

export function NovelInstructionsTab({ id }: { id: number }) {
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let cancelled = false
    getInstructions(id).then(
      (i) => !cancelled && setSaved(i.project_instructions),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [id])
  return (
    <section className="panel" aria-label="Instructions">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {saved !== null && <InstructionsForm id={id} saved={saved} onSaved={setSaved} />}
    </section>
  )
}
