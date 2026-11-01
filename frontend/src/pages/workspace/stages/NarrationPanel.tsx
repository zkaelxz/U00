import { useEffect, useState } from 'react'

import { narrationApi } from '../../../api/dub'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { NarrationConfig } from '../../../types/dub'
import { AI_ENGINE_LABEL } from '../../../helpText'
import { useNovelFilesVersion } from './novelFileEvents'
import { EngineGateNote } from './EngineGateNote'
import { useEngineGate, useTitleEngines } from './useEngineGate'

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
  // Not remembered: resuming an interrupted run is the default.
  const [startOver, setStartOver] = useState(false)
  const [error, setError] = useState<unknown>(null)

  // Attaching the text bumps this, so the panel stops saying there is none.
  const filesVersion = useNovelFilesVersion()
  useEffect(() => {
    let cancelled = false
    narrationApi.config(dramaId).then(
      (c) => {
        if (cancelled) return
        setCfg(c)
        setEngine((current) => current || c.default_engine)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, filesVersion])

  // The title's engine list knows the SDK and Ollama; this panel's own key flags are the ones its run checks.
  const titleEngines = useTitleEngines(dramaId)
  const ownEngines = cfg?.engines.map((o) => ({ name: o.key, label: o.key, free: false, models: null, key_configured: o.key_configured }))
  const titleGate = useEngineGate(titleEngines.engines, engine)
  const ownGate = useEngineGate(ownEngines, engine)
  const gate = titleGate ?? ownGate

  if (!cfg) return error ? <ErrorBanner error={error} /> : null
  if (!cfg.is_narration) return null

  const needsConfirm = cfg.replaces_existing_lines
  const disabled =
    busy || cfg.job_running || !cfg.has_novel_source || !engine || !!gate || (needsConfirm && !confirmed)

  const start = () =>
    narrationApi
      .run(dramaId, { engine, ...(model.trim() ? { model: model.trim() } : {}) }, undefined, { fresh: startOver })
      .then((r) => {
        setError(null)
        setConfirmed(false)
        onJobStarted(r.job_id)
      }, setError)

  const summary = !cfg.has_novel_source
    ? 'Attach the novel text on the Media stage first'
    : cfg.job_running
      ? 'A chunk-and-tag job is already running'
      : `${engine ? humanize('engine', engine) : 'no engine'}${model.trim() ? ` · ${model.trim()}` : ''}`

  return (
    <Section storageKey="dub.narration" title="Chunk and tag speakers" summary={summary}>
      {!cfg.has_novel_source && <p className="muted">Attach the novel text on the Media stage first.</p>}
      <div className="dub-grid">
        <Field
          label={AI_ENGINE_LABEL}
          help={`Splits the novel into narration lines of up to ${cfg.max_chunk_chars} characters and tags who speaks each one.`}
        >
          <select value={engine} onChange={(e) => setEngine(e.target.value)}>
            {cfg.engines.map((o) => (
              <option key={o.key} value={o.key}>
                {humanize('engine', o.key)}
                {o.key_configured ? '' : ' (no key set)'}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model" help="Optional; leave blank for the engine's default.">
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </Field>
      </div>
      <EngineGateNote gate={gate} />
      {needsConfirm && (
        <div className="dub-check">
          <Field label={`Replace the ${cfg.existing_line_count} existing lines`}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          </Field>
        </div>
      )}
      <div className="setting-list">
        <Field label="Start over" help="Ignore the batches an interrupted run already tagged and tag everything again.">
          <Toggle checked={startOver} onChange={setStartOver} />
        </Field>
      </div>
      {cfg.job_running && <p className="muted">A chunk-and-tag job is already running.</p>}
      <div className="dub-actions">
        <button type="button" className={buttonClass('secondary')} disabled={disabled} onClick={start}>
          Chunk and tag
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
