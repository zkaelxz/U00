import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import {
  createDrama, getCosts, getHistory, getPresets, getRecent, getSeries, getStats, getVoiceBank,
  searchLines,
} from '../api/library'
import { DramaDetailPanel } from '../components/DramaDetailPanel'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { LibraryList } from '../components/LibraryList'
import type { DramaCreateRequest, LibrarySearchHit } from '../types/library'
import {
  MEDIA_TYPES, NEW_SERIES, SOURCE_LANGUAGES, buildCreateRequest, groupHistory, showFold, validateCreate,
  type CreateExtras,
} from './libraryForm'
import { lineNumber } from '../lineNumber'

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

// A collapsed-by-default section; the summary carries the count, so nothing is an empty box.
function Fold({ title, count, error, children }: {
  title: string; count?: number; error: unknown; children: ReactNode
}) {
  if (!showFold(count, error)) return null
  return (
    <details className="panel fold">
      <summary>{title}{count !== undefined && ` (${count})`}</summary>
      <section aria-label={title}>
        <ErrorBanner error={error} />
        {children}
      </section>
    </details>
  )
}

function StatsStrip({ reloadKey }: { reloadKey: number }) {
  const stats = useLoad(getStats, reloadKey)
  const s = stats.data
  return (
    <section className="wide stats-strip" aria-label="Stats">
      <ErrorBanner error={stats.error} />
      {s && (
        <p data-testid="stats">
          {s.total_dramas} drama(s) · {s.translated_lines}/{s.total_lines} lines translated · $
          {s.usage.estimated_cost_usd.toFixed(2)} spent
        </p>
      )}
    </section>
  )
}

function MoreSections({ reloadKey }: { reloadKey: number }) {
  const recent = useLoad(getRecent, reloadKey)
  const series = useLoad(getSeries, reloadKey)
  const costs = useLoad(getCosts, reloadKey)
  const history = useLoad(getHistory, reloadKey)
  const presets = useLoad(getPresets, reloadKey)
  const voices = useLoad(getVoiceBank, reloadKey)
  const grouped = history.data ? groupHistory(history.data.items) : undefined
  return (
    <div className="more-grid wide">
      <Fold title="Recently active" count={recent.data?.items.length} error={recent.error}>
        <ul>
          {recent.data?.items.map((d) => (
            <li key={d.id}>{name(d)} <span className="muted">{d.status}</span></li>
          ))}
        </ul>
      </Fold>
      <Fold title="Series" count={series.data?.items.length} error={series.error}>
        <ul>
          {series.data?.items.map((x) => (
            <li key={x.id}>{x.name} <span className="muted">{x.dramas.length} dramas</span></li>
          ))}
        </ul>
      </Fold>
      <Fold title="Cost by drama" count={costs.data?.items.length} error={costs.error}>
        <ul>
          {costs.data?.items.map((c) => (
            <li key={c.id}>{name(c)} <span className="muted">${c.estimated_cost_usd.toFixed(2)}</span></li>
          ))}
        </ul>
      </Fold>
      <Fold title="Reading history" count={grouped?.length} error={history.error}>
        <ul>
          {grouped?.map(({ entry: h, count }) => (
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
      </Fold>
      <Fold title="Presets" count={presets.data?.items.length} error={presets.error}>
        <ul>
          {presets.data?.items.map((p) => (
            <li key={p.id}>{p.name} <span className="muted">{p.translation_engine}</span></li>
          ))}
        </ul>
      </Fold>
      <Fold title="Voice bank" count={voices.data?.items.length} error={voices.error}>
        <ul>
          {voices.data?.items.map((v) => (
            <li key={v.id}>{v.name} <span className="muted">{v.language}</span></li>
          ))}
        </ul>
      </Fold>
    </div>
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
    <details className="panel fold wide">
      <summary>Search all lines</summary>
      <section aria-label="Search lines">
      <form onSubmit={submit} className="stack">
        <input
          type="search"
          aria-label="Search all lines"
          maxLength={200}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
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
              {name({ ...h, id: h.drama_id })} #{lineNumber(h.idx)}
            </button>{' '}
            {h.zh} {h.en && <span className="muted">{h.en}</span>}
          </li>
        ))}
      </ul>
      </section>
    </details>
  )
}

const NO_EXTRAS: CreateExtras = { series: '', newSeriesName: '', preset: '' }

function CreateForm({ onCreated, reloadKey }: { onCreated: (id: number) => void; reloadKey: number }) {
  const [form, setForm] = useState<DramaCreateRequest>({
    source_language: 'zh', media_type: 'audio_drama', title_en: '', title_zh: '',
    author: '', studio: '', director: '', voice_actors: '',
  })
  const [extras, setExtras] = useState<CreateExtras>(NO_EXTRAS)
  const series = useLoad(getSeries, reloadKey)
  const presets = useLoad(getPresets, reloadKey)
  const setExtra = (k: keyof CreateExtras) => (e: { target: { value: string } }) =>
    setExtras({ ...extras, [k]: e.target.value })
  const [error, setError] = useState<unknown>(null)
  const [invalid, setInvalid] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const set = (k: keyof DramaCreateRequest) => (e: { target: { value: string } }) =>
    setForm({ ...form, [k]: e.target.value })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body = buildCreateRequest(form, extras)
    const problem = validateCreate(body)
    setInvalid(problem)
    if (problem) return
    createDrama(body).then(
      (d) => {
        setError(null)
        setForm({ ...form, title_en: '', title_zh: '', author: '', studio: '', director: '', voice_actors: '' })
        setExtras(NO_EXTRAS)
        setOpen(false)
        onCreated(d.id)
      },
      (err: unknown) => setError(err),
    )
  }

  return (
    <details className="panel fold new-drama" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>New drama</summary>
      <section aria-label="New drama">
      <form onSubmit={submit} className="stack">
        <Field label="English title">
          <input value={form.title_en} onChange={set('title_en')} />
        </Field>
        <Field label="Original title">
          <input value={form.title_zh} onChange={set('title_zh')} />
        </Field>
        <div className="field-row">
          <Field label="Source language">
            <select value={form.source_language} onChange={set('source_language')}>
              {SOURCE_LANGUAGES.map((l) => <option key={l}>{l}</option>)}
            </select>
          </Field>
          <Field label="Media type">
            <select value={form.media_type} onChange={set('media_type')}>
              {MEDIA_TYPES.map((m) => <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>)}
            </select>
          </Field>
        </div>
        <details className="fold">
          <summary>Credits, series and preset</summary>
          <div className="stack">
            <div className="field-row">
              <Field label="Author">
                <input value={form.author} onChange={set('author')} />
              </Field>
              <Field label="Studio">
                <input value={form.studio} onChange={set('studio')} />
              </Field>
            </div>
            <div className="field-row">
              <Field label="Director">
                <input value={form.director} onChange={set('director')} />
              </Field>
              <Field label="Voice actors" help="Comma-separated.">
                <input value={form.voice_actors} onChange={set('voice_actors')} />
              </Field>
            </div>
            <div className="field-row">
              <Field label="Series" help="Dramas in one series share characters and glossary.">
                <select value={extras.series} onChange={setExtra('series')}>
                  <option value="">No series</option>
                  {series.data?.items.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}
                  <option value={NEW_SERIES}>New series…</option>
                </select>
              </Field>
              {extras.series === NEW_SERIES && (
                <Field label="New series name">
                  <input value={extras.newSeriesName} onChange={setExtra('newSeriesName')} />
                </Field>
              )}
              {!!presets.data?.items.length && (
                <Field label="Preset" help="Applies the preset's translation engine to the new drama.">
                  <select value={extras.preset} onChange={setExtra('preset')}>
                    <option value="">No preset</option>
                    {presets.data.items.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
                  </select>
                </Field>
              )}
            </div>
          </div>
        </details>
        <div className="actions">
          <button type="submit" className="primary">Create drama</button>
        </div>
      </form>
      {invalid && <p className="error" role="alert">{invalid}</p>}
      <ErrorBanner error={error} />
      </section>
    </details>
  )
}

export default function LibraryPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const reload = () => setReloadKey((k) => k + 1)

  return (
    <main className="library-grid">
      <StatsStrip reloadKey={reloadKey} />
      <div className="wide new-drama-slot">
        <CreateForm reloadKey={reloadKey} onCreated={(id) => { setSelectedId(id); reload() }} />
      </div>
      <LibraryList selectedId={selectedId} onSelect={setSelectedId} reloadKey={reloadKey} />
      {selectedId !== null && (
        <DramaDetailPanel
          key={selectedId}
          dramaId={selectedId}
          onDeleted={() => { setSelectedId(null); reload() }}
        />
      )}
      <MoreSections reloadKey={reloadKey} />
      <LineSearch onSelect={setSelectedId} />
    </main>
  )
}
