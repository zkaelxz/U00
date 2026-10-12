import { useState } from 'react'

import { planInstall, queueInstall } from '../../api/pendingInstall'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { PendingInstallPlan } from '../../types/pendingInstall'
import {
  InstallPlanPanel, needsPlanPanel, planCheckUnavailable, planPanelKey, type PlannedInstall,
} from './PendingInstall'

export type PlanPreview =
  | { kind: 'go' }
  | { kind: 'panel'; plan: PendingInstallPlan }
  | { kind: 'failed'; error: unknown }

/** What an install should do next. A preview that can't be had (older server, offline) goes ahead as before. */
export async function previewInstall(keys: string[], plan: typeof planInstall = planInstall): Promise<PlanPreview> {
  try {
    const p = await plan(keys)
    return needsPlanPanel(p) ? { kind: 'panel', plan: p } : { kind: 'go' }
  } catch (e) {
    return planCheckUnavailable(e) ? { kind: 'go' } : { kind: 'failed', error: e }
  }
}

/**
 * The preview-then-install flow every install button shares: ask the server what
 * the install would change, show the panel when something is replaced, risky,
 * refused or has to wait, and pass the ticked risks on to the install. The server
 * re-plans on the install itself, so skipping this only leaves a 409 with no control to answer it.
 */
export function useInstallPlan({ onChecking, onChecked, onQueued, onQueueFailed }: {
  /** The preview is being fetched (show busy). */
  onChecking: (label: string) => void
  /** The preview ended without an install starting (panel shown, or the check failed). */
  onChecked: () => void
  onQueued: (label: string) => void
  onQueueFailed: (label: string, error: unknown) => void
}) {
  const [planned, setPlanned] = useState<PlannedInstall | null>(null)
  const [queueing, setQueueing] = useState(false)
  const [planError, setPlanError] = useState<unknown>(null)

  const withPlan = async (label: string, keys: string[], installNow: (acceptedRisks: string[]) => Promise<void>) => {
    setPlanError(null)
    onChecking(label)
    const preview = await previewInstall(keys)
    if (preview.kind === 'go') return installNow([])
    onChecked()
    if (preview.kind === 'failed') return setPlanError(preview.error)
    setPlanned({ label, keys, plan: preview.plan, installNow: (accepted) => { setPlanned(null); void installNow(accepted) } })
  }

  const queueForRestart = async (acceptRisk: boolean) => {
    if (!planned || queueing) return
    const { label, keys } = planned
    setQueueing(true)
    try {
      const r = await queueInstall(keys, acceptRisk)
      setPlanned(null)
      // The server found nothing in use after all: install straight away.
      if (r.install_now) return void planned.installNow(acceptRisk ? planned.plan.needs_confirm : [])
      onQueued(label)
    } catch (e) {
      setPlanned(null)
      onQueueFailed(label, e)
    } finally {
      setQueueing(false)
    }
  }

  const view = (
    <>
      <ErrorBanner error={planError} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setPlanError(null)} />
      {planned && (
        <InstallPlanPanel key={planPanelKey(planned.keys)} planned={planned} queueing={queueing}
          onQueue={(risk) => void queueForRestart(risk)} onCancel={() => setPlanned(null)} />
      )}
    </>
  )
  return { planned, withPlan, view }
}
