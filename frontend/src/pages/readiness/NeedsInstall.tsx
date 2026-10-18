import { ButtonLink } from '../../components/Button'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { usePcOnly } from '../../hooks/usePcOnly'
import { refreshReadiness } from '../../hooks/useReadiness'
import { routeHref } from '../../router'
import { InstallProgress } from '../diagnostics/InstallProgress'
import type { Need } from './needs'
import { useNeedInstall } from './useNeedInstall'

export const ASK_OWNER = 'Ask the owner of the main PC to set this up.'

/** Where Diagnostics > Packages opens with the first missing package already picked. */
export const packagesHref = (need: Pick<Need, 'packages'>) =>
  routeHref({ name: 'diagnostics', card: 'packages', ...(need.packages[0] ? { pkg: need.packages[0] } : {}) })

/**
 * A missing need: why a control is off, and the way to fix it. The action installs in place only where
 * the shared install flow can (Whisper, GPU PyTorch); everything else opens
 * Diagnostics > Packages with the package picked, or Settings for a key. Without admin rights, or away
 * from the main PC, it says who to ask instead, since the buttons would only be refused.
 */
export function NeedsInstall({ need, onInstalled }: { need: Need; onInstalled?: () => void }) {
  const pc = usePcOnly()
  const admin = useHolds('admin.diagnostics')
  const install = useNeedInstall(onInstalled)
  const atPc = pc !== 'remote'
  const canInstall = admin && atPc

  const action = () => {
    if (need.action === 'recheck') {
      return <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void refreshReadiness()}>Check again</button>
    }
    // A separate program (Ollama) has no install here; its link below is for everyone.
    if (need.action === 'external') return null
    if (!admin) return <p className="muted" data-testid="needs-ask-owner">{ASK_OWNER}</p>
    if (!atPc) return null
    switch (need.action) {
      case 'install_whisper':
        return (
          <ConfirmButton name="transcription" verb="install" tone="primary" label="Install…" confirmLabel="Confirm install"
            busy={!!install.busy} disabled={!!install.blocked} onConfirm={() => void install.installWhisper()} />
        )
      case 'install_gpu_torch':
        return (
          <ConfirmButton name="GPU PyTorch" verb="set up" tone="primary" label="Set up GPU…"
            confirmLabel={`Confirm GPU setup (${need.size ?? 'about 2.5 GB'})`}
            busy={!!install.busy} disabled={!!install.blocked} onConfirm={() => void install.installGpuTorch()} />
        )
      case 'settings':
        return <ButtonLink variant="secondary" size="sm" href={routeHref({ name: 'settings' })}>Add in Settings</ButtonLink>
      default:
        return <ButtonLink variant="secondary" size="sm" href={packagesHref(need)}>Open Diagnostics</ButtonLink>
    }
  }

  return (
    <div className="needs-install" data-testid={`needs-${need.id}`}>
      <p className={need.recommended ? 'muted' : 'warn'} role="note">
        {need.reason}{need.size ? ` (${need.size})` : ''}
      </p>
      <div className="actions">
        {action()}
        {need.link && (
          <a className={buttonClass('ghost', 'sm')} href={need.link.href} target="_blank" rel="noopener noreferrer">{need.link.label} ↗</a>
        )}
      </div>
      {canInstall && install.busy && (install.busy.job
        ? <InstallProgress job={install.busy.job} name={install.busy.name} />
        : <p className="muted" role="status">{install.busy.name.startsWith('Checking') ? `${install.busy.name}…` : 'Starting the install…'}</p>)}
      {canInstall && !install.busy && install.blocked && ['install_whisper', 'install_gpu_torch'].includes(need.action) && (
        <p className="muted" role="note">{install.blocked}</p>
      )}
      {install.note && <p className="muted" role="status">{install.note}</p>}
      <ErrorBanner error={install.failure} describe={{ pcOnly: true, serverText: true }} onDismiss={() => install.setFailure(null)} />
      {install.planView}
    </div>
  )
}
