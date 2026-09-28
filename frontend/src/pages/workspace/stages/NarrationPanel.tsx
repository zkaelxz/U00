import { useEffect, useState } from 'react'

import { narrationApi } from '../../../api/dub'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type { NarrationConfig } from '../../../types/dub'

interface Props {
  dramaId: number
  busy: boolean
  onJobStarted: (jobId: string) => void
}

export function NarrationPanel({ dramaId, busy, onJobStarted }: Props) {
  const [cfg, setCfg] = useState<NarrationConfig | null>(null)
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    narrationApi.config(dramaId).then(
      (c) => {
        if (cancelled) return
        setCfg(c)
        setEngine(c.default_engine)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (!cfg) return error ? <ErrorBanner error={error} /> : null
  if (!cfg.is_narration) return null

  const needsConfirm = cfg.replaces_existing_lines
  const disabled =
    busy || cfg.job_running || !cfg.has_novel_source || !engine || (needsConfirm && !confirmed)

  const start = () =>
    narrationApi
      .run(dramaId, { engine, ...(model.trim() ? { model: model.trim() } : {}) })
      .then((r) => {
        setError(null)
        setConfirmed(false)
        onJobStarted(r.job_id)
      }, setError)

  return (
    <section className="panel" aria-label="Chunk and tag speakers">
      <h3>Chunk and tag speakers</h3>
      {!cfg.has_novel_source && <p className="muted">Attach the novel text on the Source stage first.</p>}
      <p className="muted">
        Splits the novel into narration lines of up to {cfg.max_chunk_chars} characters and tags who
        speaks each one.
      </p>
      <div className="dub-grid">
        <label>
          Engine
          <select value={engine} onChange={(e) => setEngine(e.target.value)}>
            {cfg.engines.map((o) => (
              <option key={o.key} value={o.key}>
                {o.key}
                {o.key_configured ? '' : ' (no key set)'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model (optional)
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
      </div>
      {needsConfirm && (
        <label className="inline">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          Replace the {cfg.existing_line_count} existing lines
        </label>
      )}
      {cfg.job_running && <p className="muted">A chunk-and-tag job is already running.</p>}
      <button type="button" disabled={disabled} onClick={start}>
        Chunk and tag
      </button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}
