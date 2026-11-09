/*
 * Diagnostics > Models > Memory by process: which Python, Ollama and llama.cpp
 * processes hold RAM and graphics memory, and whether this server started them.
 * Read-only, on demand (Refresh; no polling), PC only. It answers "Models in
 * memory says nothing, so what holds the memory?"
 */
import { useCallback, useEffect, useState } from 'react'

import { getProcessMemory, type ProcessMemory } from '../../api/processMemory'
import { Card } from '../../components/Card'
import { CardFoldContext } from '../../components/cardFold'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { formatDecimalBytes } from '../libraryAdmin/libraryAdmin'
import { refreshedLine } from './loadedModelsModel'
import { RELATION_LABEL, STATE_NOTE, gpuGapNote, summaryLine, vramCell } from './processMemoryModel'

const TITLE = 'Memory by process'

export function ProcessMemoryCard({ openSignal = 0 }: { openSignal?: number }) {
  const pc = usePcOnly()
  const [data, setData] = useState<ProcessMemory | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(true)

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      setData(await getProcessMemory())
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }, [])

  // First read starts with busy already true, so the effect sets no state before its await.
  useEffect(() => {
    if (pc === 'remote') return
    let live = true
    getProcessMemory()
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e))
      .finally(() => live && setBusy(false))
    return () => { live = false }
  }, [pc])

  if (pc === 'remote') return null
  const gap = data ? gpuGapNote(data) : ''
  return (
    <CardFoldContext.Provider value={{ storageKey: 'diagnostics.processMemory', defaultOpen: false, summary: data?.state === 'ok' ? summaryLine(data) : undefined, openSignal }}>
    <Card
      title={TITLE}
      aria-label={TITLE}
      meta={data ? refreshedLine(data.checked_at) : undefined}
      actions={
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void load()}>
          Refresh
        </button>
      }
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data ? (
        !error && <p className="muted">Loading…</p>
      ) : data.state !== 'ok' ? (
        <p className="muted" data-testid="process-note">{STATE_NOTE[data.state]}</p>
      ) : (
        <div className="settings-form">
          <p className="muted">
            Python, Ollama and llama.cpp processes on this PC. A process that this server did not start is
            something else holding memory: another Baihe window, a worker that outlived its job, or another program.
          </p>
          {data.processes.length === 0 ? (
            <p className="muted" data-testid="process-empty">No such processes found.</p>
          ) : (
            <div className="table-scroll">
              <table className="process-table" data-testid="process-table">
                <thead>
                  <tr><th>Process</th><th>Started by</th><th>RAM</th><th>Graphics memory</th><th>Command</th></tr>
                </thead>
                <tbody>
                  {data.processes.map((r) => (
                    <tr key={r.pid}>
                      <td>{r.role} (PID {r.pid})</td>
                      <td>{RELATION_LABEL[r.relation]}</td>
                      <td>{formatDecimalBytes(r.ram_bytes)}</td>
                      <td>{vramCell(r)}</td>
                      <td className="process-command">{r.command}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {gap && <p data-testid="process-gap">{gap}</p>}
        </div>
      )}
    </Card>
    </CardFoldContext.Provider>
  )
}
