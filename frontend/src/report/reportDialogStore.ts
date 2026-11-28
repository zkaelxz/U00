// Open state of the "Report a problem" dialog, as a module store so anything
// (an error fallback, a keyboard shortcut) can open it with
// openReportDialog(). A mounted <ReportProblemHost/> renders the dialog.
let dialogOpen = false
let opener: HTMLElement | null = null
const listeners = new Set<() => void>()

function set(next: boolean) {
  if (next === dialogOpen) return
  dialogOpen = next
  listeners.forEach((l) => l())
}

export function openReportDialog(): void {
  if (!dialogOpen) opener = typeof document !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null
  set(true)
}

/** Closes the dialog and returns the element that had focus when it opened, if it is still on the page. */
export function closeReportDialog(): HTMLElement | null {
  const back = opener?.isConnected ? opener : null
  opener = null
  set(false)
  return back
}

export function isReportDialogOpen(): boolean {
  return dialogOpen
}

export function subscribeReportDialog(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
