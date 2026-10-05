import { useId, useState } from 'react'

import { installDependency } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { capFirst } from '../../labels'
import type { DiagnosticsPackageInfo, ModelEngineVersion } from '../../types/diagnostics'
import { busyLine, installBlockedReason, installConfirmLabel, type AdminBusy } from './diagnosticsAdmin'
import { packageSizeText } from './installPresets'
import { OutcomeBlock, type Outcome } from './PackagesSection'

/**
 * The size and Install… button on a not-installed model engine's row. Same
 * install call and the same page-wide busy state as Packages, so only one
 * install runs at a time; the result shows under the row.
 */
export function EngineInstall({ engine, info, torchInstalled, jobsActive, busy, onBusy, onInstalled }: {
  engine: ModelEngineVersion
  info: DiagnosticsPackageInfo | undefined
  torchInstalled: boolean
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  onInstalled: () => void
}) {
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const reasonId = useId()
  const name = engine.package as string
  const blocked = installBlockedReason(jobsActive, busy)
  const running = busyLine(busy)
  const size = info ? packageSizeText(info, torchInstalled) : null

  const run = async () => {
    onBusy({ kind: 'install', name })
    setOutcome(null)
    try {
      const r = await installDependency(name)
      setOutcome({ kind: 'install', name, ok: r.ok, output: r.output_tail, hint: r.hint })
      if (r.ok) onInstalled()
    } catch (e) {
      setOutcome({ kind: 'install', name, error: e })
    } finally {
      onBusy(null)
    }
  }

  return (
    <>
      {engine.help && <span className="muted"> · {engine.help}</span>}
      {size && <span className="muted" data-testid="pkg-size"> · {size}</span>}
      {blocked && !running && <span className="muted" id={reasonId}> · {blocked}</span>}
      {info?.not_offered_reason && <span className="muted"> · {capFirst(info.not_offered_reason)}</span>}
      {!info?.not_offered_reason && (
      <div className="actions">
        <ConfirmButton
          name={name}
          label="Install…"
          verb="install"
          tone="primary"
          confirmLabel={installConfirmLabel(name)}
          disabled={!!blocked}
          describedBy={blocked && !running ? reasonId : undefined}
          busy={busy?.name === name && busy.kind === 'install'}
          onConfirm={() => void run()}
        />
      </div>
      )}
      {outcome && <OutcomeBlock outcome={outcome} onRecheck={onInstalled} />}
    </>
  )
}
