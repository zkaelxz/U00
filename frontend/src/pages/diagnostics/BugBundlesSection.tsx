import { useCallback, useEffect, useState } from 'react'

import { deleteBugBundle, getBugBundles } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsBugBundle } from '../../types/diagnostics'
import { bugBundleTitle, bugBundleReplayText } from './diagnosticsAdmin'

/**
 * "Saved bug bundles": the frozen input/output snapshots saved from a line's
 * "What happened here?" panel. Listed with their output; deleting one is PC
 * only. (Replay stays in the old app.) Hidden when there are none.
 */
export function BugBundlesSection({ pc }: { pc: PcMode }) {
  const [items, setItems] = useState<DiagnosticsBugBundle[] | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    getBugBundles().then(setItems, () => undefined)
  }, [])
  useEffect(load, [load])

  if (!items || items.length === 0) return null
  const remove = (id: number) => {
    setBusyId(id)
    setError(null)
    deleteBugBundle(id).then(
      () => {
        setBusyId(null)
        load()
      },
      (e: unknown) => {
        setBusyId(null)
        setError(e)
      },
    )
  }
  return (
    <Section title="Saved bug bundles" count={items.length} storageKey="diagnostics.bugBundles">
      <ul className="diag-history" aria-label="Saved bug bundles">
        {items.map((b) => {
          const replay = bugBundleReplayText(b)
          return (
            <li key={b.id}>
              <details>
                <summary>{bugBundleTitle(b)}</summary>
                <p className="muted">
                  Engine: {b.engine ?? '—'} / {b.model || '—'}
                </p>
                <p>
                  <strong>Output when saved:</strong> {b.produced_output || '—'}
                </p>
                {replay && <p className={b.reproduced ? 'warn' : undefined}>{replay}</p>}
                {pc !== 'remote' && (
                  <div className="actions">
                    <ConfirmButton
                      name={`bundle #${b.id}`}
                      busy={busyId === b.id}
                      disabled={busyId !== null && busyId !== b.id}
                      onConfirm={() => remove(b.id)}
                    />
                  </div>
                )}
              </details>
            </li>
          )
        })}
      </ul>
      {pc === 'remote' && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
      <ErrorBanner error={error} describe={{ pcOnly: true }} onDismiss={() => setError(null)} />
    </Section>
  )
}
