import { useEffect, useState } from 'react'

import { deleteVdbNames, downloadVdbNames, getTitleVdbNames, getVdbNamesStatus, setTitleVdbNames } from '../../../api/vdbNames'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { usePcOnly } from '../../../hooks/usePcOnly'
import type { VdbNamesStatus } from '../../../types/vdbNames'
import { useStage } from '../StageContext'
import { vdbAgeText, vdbNamesSummary, vdbToggleBlocker } from './vdbNamesModel'
import './languagePacks.css'

/** Optional personal list of VTuber names, downloaded on demand. Off for every title until switched on. */
export function VtuberNames() {
  const { dramaId } = useStage()
  const pc = usePcOnly()
  const [status, setStatus] = useState<VdbNamesStatus | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getVdbNamesStatus(), getTitleVdbNames(dramaId)]).then(
      ([s, t]) => {
        if (cancelled) return
        setStatus(s)
        setEnabled(t.enabled)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const run = (action: () => Promise<VdbNamesStatus>) => {
    setBusy(true)
    action().then(setStatus, setError).finally(() => setBusy(false))
  }
  const toggle = (on: boolean) => {
    setEnabled(on)
    setTitleVdbNames(dramaId, on).then((t) => setEnabled(t.enabled), (e: unknown) => {
      setEnabled(!on)
      setError(e)
    })
  }
  const blocker = vdbToggleBlocker(status)
  const remote = pc === 'remote'
  return (
    <Section title="VTuber names" storageKey="glossary.vtuberNames" summary={vdbNamesSummary(status, enabled)}>
      <div className="lp">
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
        <p className="muted">
          A personal list of VTuber names from dd-center/vdb, for your own non-commercial use. It is stored only on
          this PC, is never shared or exported, and is under the VTuber Public Database International License v1.0
          (attribution to dd-center/vdb; shared copies would need the same licence).
        </p>
        <div className="lp-actions">
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={busy || remote}
            aria-describedby={remote ? 'vdb-pc-reason' : undefined}
            title="Downloads about 2.4 MB from vdb.vtbs.moe"
            onClick={() => run(downloadVdbNames)}
          >
            {busy ? 'Working…' : status?.downloaded ? 'Download again' : 'Download VTuber names'}
          </button>
          {status?.downloaded && !remote && (
            <ConfirmButton name="VTuber names" busy={busy} onConfirm={() => run(deleteVdbNames)} />
          )}
          {remote && <span className="muted" id="vdb-pc-reason">Downloading and deleting are PC only. Run this on the main PC.</span>}
        </div>
        {status?.downloaded && (
          <p className="muted" role="status">
            {status.name_count} names from {status.entry_count} entries. {vdbAgeText(status)}
          </p>
        )}
        <div className="lp-head">
          <div>
            <strong>Use for this title</strong>
            <div className="muted">
              {blocker ?? 'Names found in this title’s text are offered under Find possible names and sent with translations as “keep as written”. Your glossary terms always win.'}
            </div>
          </div>
          <Toggle checked={enabled} disabled={!!blocker} onChange={toggle} aria-label="Use VTuber names for this title" />
        </div>
      </div>
    </Section>
  )
}
