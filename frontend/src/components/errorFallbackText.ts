// Helpers for the crash fallbacks (ErrorBoundary.tsx and bootFallback.ts).

// The text shown for a caught error: the message only (no stack), capped so
// a huge message can't swamp the page.
export function errorText(error: unknown): string {
  const text = error instanceof Error ? error.message || error.name : String(error)
  return text.length > 2000 ? `${text.slice(0, 2000)}…` : text
}

// Copies text; falls back to a hidden textarea + execCommand where the async
// clipboard API is missing (plain http on a LAN address, older WebView).
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the textarea route
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch {
    return false
  }
}
