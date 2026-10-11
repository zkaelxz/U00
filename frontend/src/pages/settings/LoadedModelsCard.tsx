/*
 * Settings > Loaded now: what Ollama, this app and the GPU hold in memory.
 * Read-only and on demand (Refresh; no polling). "Free app models" drops the
 * app's own cached models behind a confirm, PC only, and is disabled while a
 * GPU job runs. Ollama's own unload is not here.
 */
import { useCallback, useEffect, useState } from 'react'

import { freeAppModels, getLoadedModels, type LoadedModels } from '../../api/loadedModels'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { FREE_BUSY_NOTE, OLLAMA_EMPTY, gpuLine, llamaLine, loadedRows, memoryLine, refreshedLine, unreadableReserveNote } from './loadedModelsModel'

const TITLE = 'Loaded now'

export function LoadedModelsCard() {
  const pc = usePcOnly()
  const [data, setData] = useState<LoadedModels | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const run = useCallback(async (action: () => Promise<LoadedModels>) => {
    setBusy(true)
    setError(null)
    try {
      setData(await action())
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void run(() => getLoadedModels())
  }, [run])

  const rows = data ? loadedRows(data) : []
  return (
    <Card
      title={TITLE}
      aria-label={TITLE}
      meta={data ? refreshedLine(data.checked_at) : undefined}
      actions={
        <button type="button" className={buttonClass('secondary')} disabled={busy}
          onClick={() => void run(() => getLoadedModels())}>
          Refresh
        </button>
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="settings-form">
          {rows.length === 0 ? (
            <p className="muted" data-testid="loaded-empty">
              Nothing loaded. {data.ollama.state !== 'running' ? OLLAMA_EMPTY[data.ollama.state] : ''}
              {data.app.state === 'unavailable' ? ' App models are unavailable.' : ''}
            </p>
          ) : (
            <div className="table-scroll">
              <table data-testid="loaded-table">
                <thead>
                  <tr><th>Model</th><th>Runs on</th><th>Size</th></tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}><td>{r.name}</td><td>{r.where}</td><td>{r.size}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rows.length > 0 && data.ollama.state !== 'running' && <p className="muted">{OLLAMA_EMPTY[data.ollama.state]}</p>}
          {rows.length > 0 && data.app.state === 'unavailable' && <p className="muted">App models are unavailable.</p>}
          <p data-testid="loaded-gpu">{gpuLine(data.gpu)}</p>
          <p data-testid="loaded-memory">{memoryLine(data.memory)}</p>
          {unreadableReserveNote(data.memory) && (
            <p className="muted" data-testid="loaded-memory-note">{unreadableReserveNote(data.memory)}</p>
          )}
          <p className="muted" data-testid="loaded-llama">{llamaLine(data.llama_cpp_running)}</p>
          {pc === 'local' && (
            <div className="settings-actions">
              <ConfirmButton
                name="app models"
                label="Free app models…"
                verb="free"
                tone="primary"
                busy={busy}
                disabled={data.gpu_job_running || data.app.models.length === 0}
                describedBy={data.gpu_job_running ? 'loaded-busy' : undefined}
                onConfirm={() => void run(freeAppModels)}
              />
              {data.gpu_job_running && <span id="loaded-busy" className="muted">{FREE_BUSY_NOTE}</span>}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
