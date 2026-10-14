// Changelog: a plain-English summary of the commits between two git refs.
import { useState } from 'react'

import { generateChangelog } from '../../api/assistant'
import { Section } from '../../components/Section'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { ChangelogResponse } from '../../types/assistant'
import { useStoredState } from '../../hooks/useStoredRun'
import { CopyButton } from './CopyButton'
import { MODE_OFF_TEXT, assistantErrorText, plural } from './assistantFormat'

// The form and the last summary, kept in this browser (cleared at sign-out).
interface SavedChangelog {
  from: string
  to: string
  result: ChangelogResponse | null
}
const NO_CHANGELOG: SavedChangelog = { from: '', to: 'HEAD', result: null }
const isSavedChangelog = (v: unknown): v is SavedChangelog =>
  typeof v === 'object' && v !== null && typeof (v as SavedChangelog).from === 'string' && typeof (v as SavedChangelog).to === 'string'

type Props = { engine: string; model: string; onModeOff: () => void }

export function ChangelogCard({ engine, model, onModeOff }: Props) {
  const [saved, setSaved] = useStoredState<SavedChangelog>('assistant.changelog', NO_CHANGELOG, isSavedChangelog)
  const { from, to, result } = saved
  const setFrom = (v: string) => setSaved((s) => ({ ...s, from: v }))
  const setTo = (v: string) => setSaved((s) => ({ ...s, to: v }))
  const setResult = (r: ChangelogResponse | null) => setSaved((s) => ({ ...s, result: r }))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = () => {
    if (!from.trim() || busy) return
    setBusy(true)
    setError(null)
    // A failure must not leave the previous refs' summary standing under it.
    setResult(null)
    generateChangelog(from, to, engine, model).then(
      (r) => {
        setResult(r)
        setBusy(false)
      },
      (e: unknown) => {
        const text = assistantErrorText(e)
        setError(text)
        setBusy(false)
        if (text === MODE_OFF_TEXT) onModeOff()
      },
    )
  }

  return (
    <section aria-label="Changelog">
      <Section title="Changelog" summary="Summarise the commits between two versions" storageKey="assistant.changelog" defaultOpen>
      <form
        className="assistant-changelog-form"
        onSubmit={(e) => {
          e.preventDefault()
          run()
        }}
      >
        <Field label="From" help="A tag, branch or commit, e.g. v0.9.">
          <input type="text" value={from} spellCheck={false} autoComplete="off" placeholder="e.g. v0.9" onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="To" help="Blank or HEAD means the current version.">
          <input type="text" value={to} spellCheck={false} autoComplete="off" onChange={(e) => setTo(e.target.value)} />
        </Field>
        <button type="submit" className={buttonClass('primary')} disabled={busy || !from.trim()}>
          {busy ? 'Generating…' : 'Generate'}
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div className="assistant-changelog" data-testid="changelog-result">
          <p className="muted">
            {plural(result.commit_count, 'commit')} from {result.from_ref} to {result.to_ref}
            {result.truncated ? ' (only the newest were summarised)' : ''}
          </p>
          <pre className="assistant-pre">{result.changelog}</pre>
          <CopyButton text={result.changelog} label="Copy changelog" />
        </div>
      )}
      </Section>
    </section>
  )
}
