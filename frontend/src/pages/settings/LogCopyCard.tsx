// Settings > Weekly log copy: the Send logs zip, saved by itself once a week into a folder on the Baihe PC.
import { useState } from 'react'

import { getLogCopy, setLogCopyFolder, type LogCopyState } from '../../api/logCopy'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useLoad } from '../../hooks/useLoad'

function Form({ state, onSaved }: { state: LogCopyState; onSaved: (s: LogCopyState) => void }) {
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const save = (folder: string) => {
    setBusy(true)
    setError(null)
    setLogCopyFolder(folder).then(
      (s) => { setBusy(false); setPath(''); onSaved(s) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  return (
    <>
      <p className="muted">
        Once a week Baihe saves the same cleaned logs zip as Send logs (keys, emails and folder names removed) into a
        folder you pick, and keeps the last {state.keep}. It stays on this PC. Off until a folder is saved.
      </p>
      <Field label="Folder on the Baihe PC"
        help={state.enabled ? `On: saving into ${state.folder_name}.` : 'Off. Full path, for example D:\\BaiheLogs.'}>
        <input type="text" value={path} disabled={busy} onChange={(e) => setPath(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={busy || !path.trim()} onClick={() => save(path)}>
          Save folder
        </button>
        {state.enabled && (
          <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => save('')}>Turn off</button>
        )}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}

export function LogCopyCard() {
  const state = useLoad(getLogCopy, 0)
  const [latest, setLatest] = useState<LogCopyState | null>(null)
  const current = latest ?? state.data
  return (
    <Card title="Weekly log copy">
      <ErrorBanner error={state.error} />
      {!current && !state.error && <p className="muted">Loading…</p>}
      {current && <Form key={`${current.enabled}-${current.folder_name}`} state={current} onSaved={setLatest} />}
    </Card>
  )
}
