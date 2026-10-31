import { useEffect, useState } from 'react'

import { getGpuTorch } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { useHolds } from '../../hooks/useHolds'
import { usePcOnly } from '../../hooks/usePcOnly'
import { useReadiness } from '../../hooks/useReadiness'
import type { DiagnosticsGpuTorchStatus, DiagnosticsSetupChecks } from '../../types/diagnostics'
import { NeedsInstall } from '../readiness/NeedsInstall'
import { useNeedInstall } from '../readiness/useNeedInstall'
import { InstallProgress } from './InstallProgress'
import { recommendedRows, recommendedSummary } from '../readiness/recommended'

/** What was found on this PC, as short lines: the GPU and driver, FFmpeg with libass, a JavaScript runtime. */
export function hardwareLines(setup: DiagnosticsSetupChecks | null, gpu: DiagnosticsGpuTorchStatus | null): { label: string; value: string; ok: boolean }[] {
  const nvidia = gpu?.nvidia
  return [
    {
      label: 'GPU',
      ok: !!nvidia?.found,
      value: !gpu ? 'checking…' : nvidia?.found ? `${nvidia.gpu_name ?? 'NVIDIA GPU'}, driver ${nvidia.driver_version ?? 'unknown'}` : 'No NVIDIA GPU found (transcription runs on the CPU, which is slower)',
    },
    {
      label: 'FFmpeg',
      ok: !!setup?.ffmpeg.found && setup.ffmpeg.libass !== false,
      value: !setup ? 'checking…' : !setup.ffmpeg.found ? 'Not found' : setup.ffmpeg.libass === false ? 'Found, without libass (burned-in subtitles need it)' : 'Found, with libass',
    },
    {
      label: 'JavaScript runtime',
      ok: !!setup?.js_runtime.found,
      value: !setup ? 'checking…' : setup.js_runtime.found ? `Found (${setup.js_runtime.name})` : 'Not found (some video downloads need one)',
    },
  ]
}

/**
 * "Recommended installs": a get-started view for a fresh PC. The hardware that was found, then one row per
 * optional piece with Ready or Missing, why you would want it, its size and the way to install it. The
 * installs are the ones the rest of Diagnostics already has; nothing here installs by itself.
 */
export function RecommendedInstalls({ setup, openSignal }: { setup: DiagnosticsSetupChecks | null; openSignal?: number }) {
  const { readiness } = useReadiness()
  const [gpu, setGpu] = useState<DiagnosticsGpuTorchStatus | null>(null)
  useEffect(() => {
    let live = true
    getGpuTorch().then((g) => live && setGpu(g), () => undefined)
    return () => { live = false }
  }, [])
  const rows = readiness ? recommendedRows(readiness) : null
  const admin = useHolds('admin.diagnostics')
  const atPc = usePcOnly() !== 'remote'
  const install = useNeedInstall()
  // Only what this card can install in place; a token, a separate program or the Tesseract binary stays a manual step.
  const installable = (rows ?? []).flatMap((r) => r.needs)
    .filter((n) => n.action === 'install_gpu_torch' || n.action === 'install_whisper' || (n.action === 'diagnostics' && n.packages.length > 0))
  return (
    <Section title="Recommended installs" storageKey="diagnostics.recommended" openSignal={openSignal}
      summary={rows ? recommendedSummary(rows) : 'Checking…'}>
      <div className="diag-stack" data-testid="recommended-installs">
        <ul className="diag-rows" aria-label="Hardware found">
          {hardwareLines(setup, gpu).map((h) => (
            <li key={h.label} data-testid={`hardware-${h.label}`}>
              <strong>{h.label}</strong> <Badge tone={h.ok ? 'ok' : 'warn'}>{h.ok ? 'Found' : 'Missing'}</Badge> <span className="muted">{h.value}</span>
            </li>
          ))}
        </ul>
        {admin && atPc && installable.length > 0 && (
          <div className="actions">
            <ConfirmButton name="everything missing" verb="install" tone="primary" label="Install all…" confirmLabel="Confirm install all"
              busy={!!install.busy} disabled={!!install.blocked} onConfirm={() => void install.installAll(installable)} />
          </div>
        )}
        {install.busy && (install.busy.job
          ? <InstallProgress job={install.busy.job} name={install.busy.name} />
          : <p className="muted" role="status">Starting the install…</p>)}
        {install.note && <p className="muted" role="status">{install.note}</p>}
        <ErrorBanner error={install.failure} describe={{ pcOnly: true, serverText: true }} onDismiss={() => install.setFailure(null)} />
        {install.planView}
        {!rows && <p className="muted">Checking what is installed…</p>}
        {rows && (
          <ul className="preflight-rows" aria-label="Recommended installs">
            {rows.map((r) => (
              <li key={r.id} className="preflight-row" data-testid={`recommended-${r.id}`}>
                <strong className="preflight-label">{r.label}</strong>
                <span className="preflight-state">
                  <Badge tone={r.ready ? 'ok' : r.required ? 'warn' : 'info'}>{r.ready ? 'Ready' : 'Missing'}</Badge>{' '}
                  {r.required ? 'Required' : 'Optional'}
                </span>
                <div className="preflight-action">
                  <p className="muted">{r.why} Size: {r.size}.</p>
                  {r.needs.map((n) => <NeedsInstall key={n.id} need={n} />)}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
