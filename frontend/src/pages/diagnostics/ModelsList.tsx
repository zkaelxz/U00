import { useState, type ReactNode } from 'react'

import { deleteHfRevision, deleteModelFile } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { CardLink } from './CardLink'
import type { DiagnosticsCard } from './diagnosticsTabs'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsHfCacheEntry, DiagnosticsModelCache, ModelEngineVersion } from '../../types/diagnostics'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { MODEL_FOLDER_LABELS, engineRow, hasModelCache, modelCacheSummary, reconcileModels } from './diagnosticsAdmin'

/**
 * "Models": each model engine with its status and the Hugging Face downloads
 * that belong to it (size, revision, Delete…), then downloads that match no
 * engine and the other model files (PyTorch hub, vocal separation). Installing
 * an engine's package happens on the Packages and installs tab, not here: a
 * not-installed row says what it is for and links there. Deleting is PC only
 * (two-step confirm); a deleted model downloads again the next time a feature
 * needs it.
 */
export function ModelsList({ engines, cache, pc, onChanged, onGoTo, openSignal }: {
  engines: ModelEngineVersion[]
  cache: DiagnosticsModelCache | null
  pc: PcMode
  onChanged: () => void
  onGoTo: (card: DiagnosticsCard) => void
  openSignal?: number
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const hasCache = hasModelCache(cache)
  if (engines.length === 0 && !hasCache) return null

  const run = (key: string, label: string, call: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    setNotice(null)
    call().then(
      () => {
        setBusy(null)
        setNotice(`Deleted ${label}.`)
        onChanged()
      },
      (e: unknown) => {
        setBusy(null)
        setError(e)
      },
    )
  }
  const canDelete = pc !== 'remote'
  const del = (key: string, name: string, call: () => Promise<unknown>) =>
    canDelete && (
      <ConfirmButton name={name} busy={busy === key} disabled={busy !== null && busy !== key}
        onConfirm={() => run(key, name, call)} />
    )
  const hfRow = (e: DiagnosticsHfCacheEntry) => {
    const key = `hf:${e.revision}`
    return (
      <li key={`${e.repo_id}@${e.revision}`}>
        <span>
          {e.repo_id} ({e.repo_type}) · {formatBytes(e.size_bytes)} · <code>{e.revision.slice(0, 12)}</code>
        </span>
        {del(key, e.repo_id, () => deleteHfRevision(e.revision))}
      </li>
    )
  }
  const group = (title: string, label: string, items: ReactNode[]) =>
    items.length > 0 && (
      <>
        <h4>{title}</h4>
        <ul className="diag-rows diag-cache" aria-label={label}>{items}</ul>
      </>
    )

  const { rows, other } = reconcileModels(engines, cache?.hf_cache ?? [])
  return (
    <Section title="Models" storageKey="diagnostics.models" summary={cache && hasCache ? modelCacheSummary(cache) : 'No downloads yet'} openSignal={openSignal}>
      {rows.length > 0 && (
        <>
          {cache && hasCache && <p className="muted">Downloaded: {modelCacheSummary(cache)}</p>}
          <ul className="diag-rows model-engines" aria-label="Model engines">
            {rows.map(({ engine, cached, notDownloaded }) => (
              <li key={engine.name}>
                <span>{engineRow(engine)}{notDownloaded && <span className="muted"> · not downloaded</span>}</span>
                {!engine.installed && (
                  <span className="muted" data-testid="engine-not-installed">
                    {engine.help && <> · {engine.help}</>}
                    {engine.package && <> · <CardLink card="packages" onGoTo={onGoTo}>Install it in Packages and installs</CardLink></>}
                  </span>
                )}
                {cached.length > 0 && (
                  <ul className="diag-rows diag-cache" aria-label={`${engine.name} downloads`}>{cached.map(hfRow)}</ul>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {group('Other downloaded models', 'Downloaded models', other.map(hfRow))}
      {cache && group('Other model files', 'Model files', cache.model_files.map((m) => (
        <li key={`${m.folder}:${m.name}`}>
          <span>{m.name} ({MODEL_FOLDER_LABELS[m.folder]}) · {formatBytes(m.size_bytes)}</span>
          {del(`file:${m.folder}:${m.name}`, m.name, () => deleteModelFile(m.folder, m.name))}
        </li>
      )))}
      {hasCache && (
        <p className="muted">
          {canDelete
            ? 'A deleted model downloads again when a feature needs it. Deleting waits for running jobs.'
            : PC_ONLY_DELETE_NOTE}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setError(null)} />
    </Section>
  )
}
