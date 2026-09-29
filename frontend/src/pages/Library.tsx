import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import {
  createDrama, getCosts, getHistory, getPresets, getRecent, getSeries, getStats, getVoiceBank,
  searchLines,
} from '../api/library'
import { DramaDetailPanel } from '../components/DramaDetailPanel'
import { ErrorBanner } from '../components/ErrorBanner'
import { LibraryList } from '../components/LibraryList'
import type { DramaCreateRequest, LibrarySearchHit } from '../types/library'
import { MEDIA_TYPES, SOURCE_LANGUAGES, groupHistory, validateCreate } from './libraryForm'

const name = (d: { title_en: string | null; title_zh: string | null; id?: number }) =>
  d.title_en || d.title_zh || `#${d.id ?? ''}`

// The API stores naive UTC timestamps (datetime.utcnow().isoformat()).
const readTime = (iso: string) =>
  new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString()

// Loads once; a failed panel shows its own banner instead of blanking the page.
function useLoad<T>(load: () => Promise<T>, reloadKey: number) {
  const [state, setState] = useState<{ data: T | null; error: unknown }>({ data: null, error: null })
  useEffect(() => {
    let cancelled = false
    load().then(
      (data) => !cancelled && setState({ data, error: null }),
      (error: unknown) => !cancelled && setState({ data: null, error }),
    )
    return () => {
      cancelled = true
    }
    // load is a stable module-level function.
  }, [load, reloadKey])
  return state
}

function Panel({ title, error, children }: { title: string; error: unknown; children: ReactNode }) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      <ErrorBanner error={error} />
      {children}
    </section>
  )
}

function Summaries({ reloadKey }: { reloadKey: number }) {
  const stats = useLoad(getStats, reloadKey)
  const recent = useLoad(getRecent, reloadKey)
  const series = useLoad(getSeries, reloadKey)
  const costs = useLoad(getCosts, reloadKey)
  const history = useLoad(getHistory, reloadKey)
  const presets = useLoad(getPresets, reloadKey)
  const voices = useLoad(getVoiceBank, reloadKey)
  const s = stats.data
  return (
    <>
      <Panel title="Stats" error={stats.error}>
        {s && (
          <p data-testid="stats">
            {s.total_dramas} drama(s) · {s.translated_lines}/{s.total_lines} lines translated · $
            {s.usage.estimated_cost_usd.toFixed(2)} spent
          </p>
        )}
      </Panel>
      <Panel title="Recently active" error={recent.error}>
        <ul>
          {recent.data?.items.map((d) => (
            <li key={d.id}>{name(d)} <span className="muted">{d.status}</span></li>
          ))}
        </ul>
      </Panel>
      <Panel title="Series" error={series.error}>
        {series.data?.items.length === 0 && <p className="muted">No series with 2+ dramas.</p>}
        <ul>
          {series.data?.items.map((x) => (
            <li key={x.id}>{x.name} <span className="muted">{x.dramas.length} dramas</span></li>
          ))}
        </ul>
      </Panel>
      <Panel title="Cost by drama" error={costs.error}>
        <ul>
          {costs.data?.items.map((c) => (
            <li key={c.id}>{name(c)} <span className="muted">${c.estimated_cost_usd.toFixed(2)}</span></li>
          ))}
        </ul>
      </Panel>
      <Panel title="Reading history" error={history.error}>
        <ul>
          {groupHistory(history.data?.items ?? []).map(({ entry: h, count }) => (
            <li key={`${h.drama_id}-${h.accessed_at}`}>
              {name({ ...h, id: h.drama_id })}
              {count > 1 && <span className="badge"> ×{count}</span>}
              <span className="muted">
                {h.percent_complete != null && ` ${Math.round(h.percent_complete)}%`}
                {h.accessed_at && ` · last read ${readTime(h.accessed_at)}`}
              </span>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Presets" error={presets.error}>
        <ul>
          {presets.data?.items.map((p) => (
            <li key={p.id}>{p.name} <span className="muted">{p.translation_engine}</span></li>
          ))}
        </ul>
      </Panel>
      <Panel title="Voice bank" error={voices.error}>
        <ul>
          {voices.data?.items.map((v) => (
            <li key={v.id}>{v.name} <span className="muted">{v.language}</span></li>
          ))}
        </ul>
      </Panel>
    </>
  )
}

function LineSearch({ onSelect }: { onSelect: (id: number) => void }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<LibrarySearchHit[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const term = q.trim()
    if (!term) return
    searchLines(term).then(
      (r) => { setHits(r.items); setError(null) },
      (err: unknown) => setError(err),
    )
  }

  return (
    <section className="panel" aria-label="Search lines">
      <h2>Search lines</h2>
      <form onSubmit={submit} className="stack">
        <label className="field">
          <span>Search all lines</span>
          <input
            type="search"
            aria-label="Search all lines"
            maxLength={200}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <div className="actions">
          <button type="submit">Search</button>
        </div>
      </form>
      <ErrorBanner error={error} />
      {hits && <p className="muted" data-testid="search-count">{hits.length} match(es)</p>}
      <ul>
        {hits?.map((h) => (
          <li key={`${h.drama_id}-${h.idx}`}>
            <button type="button" className="link" onClick={() => onSelect(h.drama_id)}>
              {name({ ...h, id: h.drama_id })} #{h.idx}
            </button>{' '}
            {h.zh} {h.en && <span className="muted">{h.en}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function CreateForm({ onCreated }: { onCreated: (id: number) => void }) {
  const [form, setForm] = useState<DramaCreateRequest>({
    source_language: 'zh', media_type: 'audio_drama', title_en: '', title_zh: '',
  })
  const [error, setError] = useState<unknown>(null)
  const [invalid, setInvalid] = useState<string | null>(null)
  const set = (k: keyof DramaCreateRequest) => (e: { target: { value: string } }) =>
    setForm({ ...form, [k]: e.target.value })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const problem = validateCreate(form)
    setInvalid(problem)
    if (problem) return
    createDrama(form).then(
      (d) => {
        setError(null)
        setForm({ ...form, title_en: '', title_zh: '' })
        onCreated(d.id)
      },
      (err: unknown) => setError(err),
    )
  }

  return (
    <section className="panel" aria-label="New drama">
      <h2>New drama</h2>
      <form onSubmit={submit} className="stack">
        <label className="field">
          <span>English title</span>
          <input value={form.title_en} onChange={set('title_en')} />
        </label>
        <label className="field">
          <span>Original title</span>
          <input value={form.title_zh} onChange={set('title_zh')} />
        </label>
        <div className="field-row">
          <label className="field">
            <span>Source language</span>
            <select value={form.source_language} onChange={set('source_language')}>
              {SOURCE_LANGUAGES.map((l) => <option key={l}>{l}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Media type</span>
            <select value={form.media_type} onChange={set('media_type')}>
              {MEDIA_TYPES.map((m) => <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>)}
            </select>
          </label>
        </div>
        <div className="actions">
          <button type="submit" className="primary">Create drama</button>
        </div>
      </form>
      {invalid && <p className="error" role="alert">{invalid}</p>}
      <ErrorBanner error={error} />
    </section>
  )
}

export default function LibraryPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const reload = () => setReloadKey((k) => k + 1)

  return (
    <main className="library-grid">
      <Summaries reloadKey={reloadKey} />
      <LineSearch onSelect={setSelectedId} />
      <CreateForm onCreated={(id) => { setSelectedId(id); reload() }} />
      <LibraryList selectedId={selectedId} onSelect={setSelectedId} reloadKey={reloadKey} />
      {selectedId !== null && (
        <DramaDetailPanel
          key={selectedId}
          dramaId={selectedId}
          onDeleted={() => { setSelectedId(null); reload() }}
        />
      )}
    </main>
  )
}
