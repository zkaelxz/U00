import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { checkGpuTorch, getGpuTorch } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { adminErrorText } from './diagnosticsAdmin'
import type { DiagnosticsGpuTorchStatus, DiagnosticsTorchVariant } from '../../types/diagnostics'
import {
  driverText, gpuTorchSummary, packageVersionText, setupBlockedReason, stateBadge, stateIsProblem, stateText, verifyText,
} from './gpuTorch'

const TORCH_PACKAGES = ['torch', 'torchvision', 'torchaudio'] as const

/**
 * Packages > "GPU PyTorch": the NVIDIA GPU and driver, then the installed
 * torch/torchvision/torchaudio beside the recommended matched set. The status
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
  const [probeError, setProbeError] = useState<string | null>(null)

  const fetchStatus = useCallback(() => getGpuTorch().then(
    (s) => { setStatus(s); setFailed(false) },
    () => setFailed(true),
  ), [])
  useEffect(() => { void fetchStatus() }, [fetchStatus, refreshKey])
  const probe = () => {
    setProbing(true)
    setProbeError(null)
    checkGpuTorch().then(setStatus, (e) => setProbeError(adminErrorText(e, 'install')))
      .finally(() => setProbing(false))
  }

  const badge = status ? stateBadge(status) : null
  const blocked = status && status.state !== 'recommended' ? setupBlockedReason(status, status.recommended) : null
  const summary = status ? gpuTorchSummary(status) : failed ? 'Unavailable' : 'Checking…'
  return (
    <Section title="GPU PyTorch" storageKey="diagnostics.gpuTorch" summary={summary} defaultOpen={!!status && stateIsProblem(status)}>
      <div className="diag-stack gpu-torch" data-testid="gpu-torch" role="group" aria-label="GPU PyTorch">
      {badge && <div><Badge tone={badge.tone}>{badge.text}</Badge></div>}
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
          <div className="table-scroll">
            <table className="torch-table" aria-label="PyTorch versions">
              <thead>
                <tr>
                  <th scope="col">Package</th>
                  <th scope="col">Installed</th>
                  <th scope="col">Recommended</th>
                </tr>
              </thead>
              <tbody>
                {[...new Set([...TORCH_PACKAGES, ...status.installed.map((x) => x.name)])].map((name) => {
                  const p = status.installed.find((x) => x.name === name) ?? { name, version: null, build: null }
                  return (
                    <tr key={name}>
                      <th scope="row">{name}</th>
                      <td>{packageVersionText(p)}</td>
                      <td>{status.recommended.versions[name] ?? '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="muted" data-testid="gpu-torch-recommended">
            Recommended: {status.recommended.label}, from <code>{status.recommended.index_url}</code>.
          </p>
          {status.problems.map((p) => <p key={p} className="warn">{p}</p>)}
          {probeError && <p className="error" role="alert">{probeError}</p>}
          {status.probe && (
            <p data-testid="gpu-torch-probe" className={status.probe.cuda_available ? 'ok' : 'warn'}>
              {verifyText(status.probe)}
            </p>
          )}
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={probe} disabled={probing} aria-busy={probing}>
              {probing ? 'Checking CUDA…' : 'Check CUDA'}
            </button>
            {status.state !== 'recommended' && action(status.recommended, blocked)}
          </div>
          {blocked && <p className="muted">{blocked}</p>}
        </>
      )}
      </div>
    </Section>
  )
}
