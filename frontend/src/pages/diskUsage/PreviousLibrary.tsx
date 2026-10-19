/*
 * "Previous library" under Library tools > Storage: the copies of the library
 * a restore left beside the library folder (it holds the media of titles
 * added since a database-only backup) and the copy kept before the last Reset
 * library, shown as one entry. Deleting needs the word DELETE typed; the
 * server refuses while a restore or job runs, or when the size changed.
 */
import { useCallback, useEffect, useState } from 'react'

import { deletePreviousLibrary, getPreviousLibrary } from '../../api/diskUsage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { TypedConfirm } from '../../components/TypedConfirm'
import { buttonClass } from '../../components/uiClasses'
import type { PreviousLibrary } from '../../types/diskUsage'
import { TabCard } from '../libraryTools/TabCard'
import { formatDecimalBytes } from './diskUsageModel'

const SERVER = { pcOnly: true, serverText: true } as const

export function PreviousLibraryCard({ onChanged }: { onChanged: (message: string) => void }) {
  const [kept, setKept] = useState<PreviousLibrary | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(() => getPreviousLibrary().then(setKept, () => setKept(null)), [])
  useEffect(() => {
    void load()
  }, [load])
  if (!kept?.present) return null
  const remove = () => {
    setBusy(true)
    setError(null)
    deletePreviousLibrary(kept.size_bytes).then(
      (r) => {
        setBusy(false)
        setConfirming(false)
        onChanged(`Deleted the previous library and freed ${formatDecimalBytes(r.freed_bytes)}.`)
        void load()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
        void load()
      },
    )
  }
  return (
    <TabCard card="previous-library">
      <section aria-label="Previous library" className="du-trash">
        <p>
          <strong>{kept.label}</strong> · {kept.partial ? 'at least ' : ''}
          {formatDecimalBytes(kept.size_bytes)} · {kept.file_count} file{kept.file_count === 1 ? '' : 's'}
        </p>
        <p className="muted du-why">
          Kept beside the library folder by a restore (with the media of titles added since a database-only backup,
          and folders that backup did not have) or before the last Reset library. Check the library before deleting it.
        </p>
        {confirming ? (
          <TypedConfirm
            word="DELETE"
            exact
            autoFocus
            action="Delete previous library permanently"
            busy={busy}
            onConfirm={remove}
            onCancel={() => setConfirming(false)}
          >
            <p className="du-warn">
              Permanently deletes the previous library ({formatDecimalBytes(kept.size_bytes)}). This can&apos;t be undone.
            </p>
          </TypedConfirm>
        ) : (
          <div className="du-actions">
            <button type="button" className={buttonClass('danger')} onClick={() => setConfirming(true)}>
              Delete previous library…
            </button>
          </div>
        )}
        <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
      </section>
    </TabCard>
  )
}
