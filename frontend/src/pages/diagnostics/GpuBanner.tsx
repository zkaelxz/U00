import { useEffect, useState } from 'react'

import { getGpuTorch } from '../../api/diagnostics'
import { getSettings, updateSetting } from '../../api/settings'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { DiagnosticsGpuTorchStatus } from '../../types/diagnostics'
import { GPU_BANNER_REMOTE, GPU_BANNER_TEXT, gpuBannerState } from './gpuBannerState'

/**
 * A calm one-click "Turn on GPU" once GPU PyTorch is there and the setting is
 * still off. It saves the same `use_gpu` setting as Settings > System and the
 * stage toggle, and hides itself once on. `status` is the caller's own read of
 * GPU PyTorch (the Diagnostics card has one); without it the banner reads it.
 */
export function GpuBanner({ status, onTurnedOn }: {
  status?: DiagnosticsGpuTorchStatus | null
  onTurnedOn?: () => void
}) {
  const pc = usePcOnly()
  const [own, setOwn] = useState<DiagnosticsGpuTorchStatus | null>(null)
  const [useGpu, setUseGpu] = useState<boolean | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const ownRead = status === undefined

  useEffect(() => {
    let cancelled = false
    // Either read can be refused (no permission) or fail: then there is no banner.
    getSettings().then((s) => !cancelled && setUseGpu(s.use_gpu), () => undefined)
    if (ownRead) getGpuTorch().then((s) => !cancelled && setOwn(s), () => undefined)
    return () => { cancelled = true }
  }, [ownRead])

  const state = gpuBannerState(ownRead ? own : status, useGpu, pc)
  if (!state) return null
  const turnOn = async () => {
    setSaving(true)
    setError(null)
    try {
      const s = await updateSetting('use_gpu', true)
      setUseGpu(s.use_gpu)
      onTurnedOn?.()
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="diag-stack" role="status" data-testid="gpu-banner">
      <p className="muted">{GPU_BANNER_TEXT}{state === 'ask_pc' && ` ${GPU_BANNER_REMOTE}`}</p>
      {state === 'turn_on' && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={saving} onClick={() => void turnOn()}>
            {saving ? 'Turning on…' : 'Turn on GPU'}
          </button>
        </div>
      )}
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setError(null)} />
    </div>
  )
}
