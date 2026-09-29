import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { getGpuTorch } from '../../api/diagnostics'
import { Section } from '../../components/Section'
import type { DiagnosticsGpuTorchStatus, DiagnosticsTorchVariant } from '../../types/diagnostics'
import {
  driverText, packageVersionText, setupBlockedReason, stateIsProblem, stateText, variantVersionsText, verifyText,
} from './gpuTorch'

/**
 * Packages > "GPU PyTorch": the NVIDIA GPU and driver, the installed
 * torch/torchvision/torchaudio, and the recommended matched set. The status
 * read is cheap (nvidia-smi and package metadata); "Check CUDA" imports torch
 * in a fresh Python on the server. `action` renders the PC-only setup button
 * for the recommended variant (the parent owns busy state and the result).
 */
export function GpuTorchPanel({ refreshKey, action }: {
  // Bumped by the parent after a setup (or any install) finishes.
  refreshKey: number
  action: (variant: DiagnosticsTorchVariant, blockedReason: string | null) => ReactNode
}) {
  const [status, setStatus] = useState<DiagnosticsGpuTorchStatus | null>(null)
  const [failed, setFailed] = useState(false)
  const [probing, setProbing] = useState(false)

  const fetchStatus = useCallback((probe: boolean) => getGpuTorch(probe).then(
    (s) => { setStatus(s); setFailed(false) },
    () => setFailed(true),
  ), [])
  useEffect(() => { void fetchStatus(false) }, [fetchStatus, refreshKey])
  const probe = () => {
    setProbing(true)
    void fetchStatus(true).finally(() => setProbing(false))
  }

  const summary = status ? stateText(status) : undefined
  return (
    <Section storageKey="diagnostics.gpuTorch" title="GPU PyTorch" summary={summary}>
      <div className="diag-stack" data-testid="gpu-torch">
        {failed && !status && <p className="muted">Couldn't read the PyTorch status.</p>}
        {!status && !failed && <p className="muted">Checking…</p>}
        {status && (
          <>
            <p className={stateIsProblem(status) ? 'warn' : status.state === 'recommended' ? 'ok' : undefined}
              data-testid="gpu-torch-state">
              {stateText(status)}
            </p>
            <p data-testid="gpu-torch-driver" className={status.nvidia.status === 'too_old' ? 'warn' : undefined}>
              {driverText(status.nvidia)}
            </p>
            <ul aria-label="Installed PyTorch packages" className="pkg-list">
              {status.installed.map((p) => (
                <li key={p.name}>
                  <span><strong>{p.name}</strong> <span className="muted">{packageVersionText(p)}</span></span>
                </li>
              ))}
            </ul>
            {status.problems.map((p) => <p key={p} className="warn">{p}</p>)}
            <p className="muted" data-testid="gpu-torch-recommended">
              Recommended ({status.recommended.label}): {variantVersionsText(status.recommended)}, from{' '}
              <code>{status.recommended.index_url}</code>.
            </p>
            {status.probe && (
              <p data-testid="gpu-torch-probe" className={status.probe.cuda_available ? 'ok' : 'warn'}>
                {verifyText(status.probe)}
              </p>
            )}
            <div className="actions">
              <button type="button" onClick={probe} disabled={probing} aria-busy={probing}>
                {probing ? 'Checking CUDA…' : 'Check CUDA'}
              </button>
              {status.state !== 'recommended' && action(status.recommended, setupBlockedReason(status, status.recommended))}
            </div>
            {status.state !== 'recommended' && setupBlockedReason(status, status.recommended) && (
              <p className="muted">{setupBlockedReason(status, status.recommended)}</p>
            )}
          </>
        )}
      </div>
    </Section>
  )
}
