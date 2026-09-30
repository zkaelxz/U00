import { useState } from 'react'

import { deleteHfRevision, deleteModelFile, deletePiperVoice } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsModelCache } from '../../types/diagnostics'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { MODEL_FOLDER_LABELS, hasModelCache, modelCacheSummary } from './diagnosticsAdmin'

/**
 * "Model cache": Hugging Face downloads, Piper voices and the files in the other
 * model folders (PyTorch hub checkpoints, vocal separation models) with their
 * sizes. Deleting one is PC only (two-step confirm); it downloads again the next
 * time a feature needs it. Hidden when every list is empty.
 */
export function ModelCacheSection({ cache, pc, onChanged }: {
  cache: DiagnosticsModelCache | null
  pc: PcMode
  onChanged: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  if (!cache || !hasModelCache(cache)) return null

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
  return (
    <Section title="Model cache" storageKey="diagnostics.cache" summary={modelCacheSummary(cache)}>
      {cache.hf_cache.length > 0 && (
        <ul className="diag-rows diag-cache" aria-label="Downloaded models">
          {cache.hf_cache.map((e) => {
            const key = `hf:${e.revision}`
            return (
              <li key={`${e.repo_id}@${e.revision}`}>
                <span>
                  {e.repo_id} ({e.repo_type}) · {formatBytes(e.size_bytes)} · <code>{e.revision.slice(0, 12)}</code>
                </span>
                {canDelete && (
                  <ConfirmButton
                    name={e.repo_id}
                    busy={busy === key}
                    disabled={busy !== null && busy !== key}
                    onConfirm={() => run(key, e.repo_id, () => deleteHfRevision(e.revision))}
                  />
                )}
              </li>
            )
          })}
        </ul>
      )}
      {cache.piper_voices.length > 0 && (
        <>
          <h4>Piper voices</h4>
          <ul className="diag-rows diag-cache" aria-label="Piper voices">
            {cache.piper_voices.map((v) => {
              const key = `piper:${v.voice}`
              return (
                <li key={v.voice}>
                  <span>
                    {v.voice} · {formatBytes(v.size_bytes)}
                  </span>
                  {canDelete && (
                    <ConfirmButton
                      name={v.voice}
                      busy={busy === key}
                      disabled={busy !== null && busy !== key}
                      onConfirm={() => run(key, v.voice, () => deletePiperVoice(v.voice))}
                    />
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
      {cache.model_files.length > 0 && (
        <>
          <h4>Other model files</h4>
          <ul className="diag-rows diag-cache" aria-label="Model files">
            {cache.model_files.map((m) => {
              const key = `file:${m.folder}:${m.name}`
              return (
                <li key={key}>
                  <span>
                    {m.name} ({MODEL_FOLDER_LABELS[m.folder]}) · {formatBytes(m.size_bytes)}
                  </span>
                  {canDelete && (
                    <ConfirmButton
                      name={m.name}
                      busy={busy === key}
                      disabled={busy !== null && busy !== key}
                      onConfirm={() => run(key, m.name, () => deleteModelFile(m.folder, m.name))}
                    />
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
      <p className="muted">
        {canDelete
          ? 'A deleted model downloads again the next time a feature needs it. Deleting waits for running jobs.'
          : PC_ONLY_DELETE_NOTE}
      </p>
      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setError(null)} />
    </Section>
  )
}
