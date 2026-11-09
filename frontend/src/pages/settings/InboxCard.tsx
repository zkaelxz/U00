// Settings > Watch folder: audio and video files dropped into one folder on the Baihe PC become titles.
import { useEffect, useState } from 'react'

import { getInbox, setInbox, type InboxState } from '../../api/inbox'
import { listAllSeries, type SeriesListItem } from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useLoad } from '../../hooks/useLoad'

const REFRESH_MS = 15000

function Form({ state, series, onSaved }: { state: InboxState; series: SeriesListItem[]; onSaved: (s: InboxState) => void }) {
  const [on, setOn] = useState(state.on)
  const [path, setPath] = useState('')
  const [language, setLanguage] = useState<InboxState['source_language']>(state.source_language)
  const [seriesId, setSeriesId] = useState(state.series_id == null ? '' : String(state.series_id))
  const [move, setMove] = useState(state.move)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const save = () => {
    setBusy(true)
    setError(null)
    setInbox({ on, path, source_language: language, series_id: seriesId ? Number(seriesId) : null, move }).then(
      (s) => { setBusy(false); setPath(''); onSaved(s) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }

  return (
    <>
      <p className="muted">
        When this is on, an audio or video file you drop into the folder becomes a title and its Transcribe goes on the
        Jobs queue (start the queue on the Jobs page). In a series with auto-run on, the series' own run takes over
        instead, inside its spending ceiling. Files are taken only once they have stopped changing, so a copy in
        progress is left alone. Your originals are never deleted.
      </p>
      <label className="checkbox-row">
        <input type="checkbox" checked={on} disabled={busy} onChange={(e) => setOn(e.target.checked)} />
        Watch the folder
      </label>
      <Field label="Folder on the Baihe PC"
        help={state.folder_set ? `Saved folder: ${state.folder_name}. Leave blank to keep it.` : 'Full path, for example D:\\Inbox.'}>
        <input type="text" value={path} disabled={busy} onChange={(e) => setPath(e.target.value)} />
      </Field>
      <Field label="Language of the audio">
        <select value={language} disabled={busy} onChange={(e) => setLanguage(e.target.value as InboxState['source_language'])}>
          <option value="ja">Japanese</option>
          <option value="zh">Chinese</option>
          <option value="ko">Korean</option>
        </select>
      </Field>
      <Field label="Add to series">
        <select value={seriesId} disabled={busy} onChange={(e) => setSeriesId(e.target.value)}>
          <option value="">None</option>
          {series.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </Field>
      <label className="checkbox-row">
        <input type="checkbox" checked={move} disabled={busy} onChange={(e) => setMove(e.target.checked)} />
        Move each file into a Done folder after it is added
      </label>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={busy} onClick={save}>Save</button>
      </div>
      {state.message && <p role="status" data-testid="inbox-message">{state.message}</p>}
      {state.imported > 0 && <p className="muted">{state.imported} added since the app started.</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}

export function InboxCard() {
  const [reloadKey, setReloadKey] = useState(0)
  const state = useLoad(getInbox, reloadKey)
  const list = useLoad(() => listAllSeries().then((r) => r.items), 0)
  const [latest, setLatest] = useState<InboxState | null>(null)
  const current = latest ?? state.data
  useEffect(() => {
    // Imports happen on the server, so the last result appears without a click.
    const t = window.setInterval(() => { setLatest(null); setReloadKey((k) => k + 1) }, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [])
  return (
    <Card title="Watch folder">
      <ErrorBanner error={state.error} />
      {!current && !state.error && <p className="muted">Loading…</p>}
      {current && <Form key={`${current.on}-${current.folder_name}`} state={current} series={list.data ?? []} onSaved={setLatest} />}
    </Card>
  )
}
