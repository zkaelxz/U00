import { useEffect, useState, type ReactNode } from 'react'

import { deleteHfRevision, deleteModelFile, getInstallPresets } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsHfCacheEntry, DiagnosticsInstallPresets, DiagnosticsModelCache, ModelEngineVersion } from '../../types/diagnostics'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { MODEL_FOLDER_LABELS, engineRow, hasModelCache, modelCacheSummary, reconcileModels, type AdminBusy } from './diagnosticsAdmin'
import { EngineInstall } from './EngineInstall'

/**
 * Setup > "Models": the one place each model engine is listed: its status,
 * Install… when it isn't installed (PC only), and under it the Hugging Face
 * downloads that belong to it (size, revision, Delete…).
 * Engines that download weights but have none show "not downloaded". Downloads
 * that match no engine and the other model files (PyTorch hub,
 * vocal separation) are their own groups. Deleting is PC only (two-step
 * confirm); a deleted model downloads again the next time a feature needs it.
 */
export function ModelsList({ engines, installable, cache, pc, jobsActive, busy: adminBusy, onBusy, onChanged, onInstalled }: {
  engines: ModelEngineVersion[]
  installable: Set<string>
  cache: DiagnosticsModelCache | null
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  onChanged: () => void
  onInstalled: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const hasCache = hasModelCache(cache)
  // Sizes are an optional extra, loaded only when an Install button will show.
  const canInstall = pc === 'local' && installable.size > 0
  const [presets, setPresets] = useState<DiagnosticsInstallPresets | null>(null)
  useEffect(() => {
    if (canInstall) getInstallPresets().then(setPresets, () => undefined)
  }, [canInstall])
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
    <>
      {rows.length > 0 && (
        <>
          <h4 id="diag-model-engines" tabIndex={-1}>Model engines</h4>
          {cache && hasCache && <p className="muted">Downloaded: {modelCacheSummary(cache)}</p>}
          <ul className="diag-rows model-engines" aria-label="Model engines">
            {rows.map(({ engine, cached, notDownloaded }) => (
              <li key={engine.name}>
                <span>{engineRow(engine)}{notDownloaded && <span className="muted"> · not downloaded</span>}</span>
                {canInstall && installable.has(engine.name) && (
                  <EngineInstall engine={engine} info={presets?.packages[engine.package as string]}
                    torchInstalled={!!presets?.packages.torch?.installed} jobsActive={jobsActive}
                    busy={adminBusy} onBusy={onBusy} onInstalled={onInstalled} />
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
    </>
  )
}
