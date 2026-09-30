// Changelog: a plain-English summary of the commits between two git refs.
import { useState } from 'react'

import { generateChangelog } from '../../api/assistant'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { ChangelogResponse } from '../../types/assistant'
import { CopyButton } from './CopyButton'
import { MODE_OFF_TEXT, assistantErrorText, plural } from './assistantFormat'

type Props = { engine: string; model: string; onModeOff: () => void }

export function ChangelogCard({ engine, model, onModeOff }: Props) {
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('HEAD')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ChangelogResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = () => {
    if (!from.trim() || busy) return
    setBusy(true)
    setError(null)
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
    <Card title="Changelog" meta="Summarise the commits between two versions." aria-label="Changelog">
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
    </Card>
  )
}
