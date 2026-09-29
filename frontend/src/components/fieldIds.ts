// Pure id/aria helpers for Field (kept out of the component file for fast refresh).
export function fieldIds(base: string, has: { help?: boolean; error?: boolean }) {
  const helpId = `${base}-help`
  const errorId = `${base}-error`
  const describedBy = [has.help ? helpId : '', has.error ? errorId : ''].filter(Boolean).join(' ')
  return { controlId: base, helpId, errorId, describedBy: describedBy || undefined }
}
