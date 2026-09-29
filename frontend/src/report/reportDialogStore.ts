// Open state of the "Report a problem" dialog, as a module store so anything
// (an error fallback, a keyboard shortcut) can open it with
// openReportDialog(). A mounted <ReportProblemButton/> renders the dialog.
let dialogOpen = false
const listeners = new Set<() => void>()

function set(next: boolean) {
  if (next === dialogOpen) return
  dialogOpen = next
  listeners.forEach((l) => l())
}

export function openReportDialog(): void {
  set(true)
}

export function closeReportDialog(): void {
  set(false)
}

export function isReportDialogOpen(): boolean {
  return dialogOpen
}

export function subscribeReportDialog(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
