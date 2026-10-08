// Pure id/aria helpers for Field (kept out of the component file for fast refresh).
export function fieldIds(base: string, has: { help?: boolean; error?: boolean }) {
  const helpId = `${base}-help`
  const errorId = `${base}-error`
  const describedBy = [has.help ? helpId : '', has.error ? errorId : ''].filter(Boolean).join(' ')
  return { controlId: base, helpId, errorId, describedBy: describedBy || undefined }
}

// A touch tap opens the help (emulated mouseenter, then focus) before its click
// arrives, so the click must toggle from the state the press found, not the
// state it finds. Keyboard clicks have no press and use the current state.
export function helpOpenAfterClick(openAtPress: boolean | null, openNow: boolean) {
  return !(openAtPress ?? openNow)
}
