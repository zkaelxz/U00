// The shared Copy helper. Copies text and says whether it worked; falls back
// to a hidden textarea + execCommand where the async clipboard API is missing
// (plain http on a LAN address, older WebView). Never throws: on false, the
// caller tells the user to select and copy the text themselves.
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the textarea route
  }
  let ta: HTMLTextAreaElement | null = null
  let focused: Element | null = null
  try {
    focused = document.activeElement
    ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    ta?.remove()
    // select() moved focus to the textarea: give it back (keyboard users).
    ;(focused as HTMLElement | null)?.focus?.()
  }
}
