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
  open: () => Promise<unknown>
  size?: 'md' | 'sm'
  className?: string
  title?: string
}) {
  const pc = usePcOnly()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  if (pc === 'remote') return null
  const run = async () => {
    setError(null)
    setBusy(true)
    try {
      await open()
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
    </>
  )
}
