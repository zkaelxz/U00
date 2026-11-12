/*
 * "Open folder": asks the server to open a folder in the PC's file manager.
 * The routes behind it are PC only (the folder opens on the PC's own screen),
 * so from another device the button is not shown.
 */
import { useState } from 'react'

import { usePcOnly } from '../hooks/usePcOnly'
import { ErrorBanner } from './ErrorBanner'
import { buttonClass } from './uiClasses'

export function OpenFolderButton({ open, size = 'md', className, title }: {
  // A background service has no desktop to open a window on: it answers { opened: false, folder } instead.
  open: () => Promise<unknown>
  size?: 'md' | 'sm'
  className?: string
  title?: string
}) {
  const pc = usePcOnly()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [byHand, setByHand] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  if (pc === 'remote') return null
  const run = async () => {
    setError(null)
    setByHand(null)
    setCopied(false)
    setBusy(true)
    try {
      const r = (await open()) as { opened?: boolean; folder?: string | null } | null
      if (r && r.opened === false && r.folder) setByHand(r.folder)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" className={buttonClass('secondary', size, className)} disabled={busy} onClick={run} title={title}>
        Open folder
      </button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {byHand && (
        <div className="banner open-folder-notice" role="status">
          <div className="open-folder-notice-body">
            <strong>Open this folder yourself.</strong>
            <div className="muted">Baihe is running as a background service on this PC, so it can't open a window. Paste this path into File Explorer:</div>
            <input type="text" readOnly value={byHand} aria-label="Folder path" style={{ width: '100%' }}
              onFocus={(e) => e.currentTarget.select()} />
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={async () => {
              try {
                await navigator.clipboard.writeText(byHand)
                setCopied(true)
              } catch {
                setCopied(false)
              }
            }}>{copied ? 'Copied' : 'Copy path'}</button>
          </div>
          <button type="button" className="link" onClick={() => setByHand(null)}>Dismiss</button>
        </div>
      )}
    </>
  )
}
