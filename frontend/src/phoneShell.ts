/*
 * The Android app's own switches. Baihe's pages run inside its WebView, which hands them `BaiheShell`
 * (android ShellSettings.Bridge); in any browser it is absent and nothing here shows.
 */
export type ShellSwitch = 'alerts_on' | 'clipboard_offer_on' | 'pip_on'

type Bridge = {
  get(name: string): boolean
  set(name: string, on: boolean): void
  // Added with picture-in-picture: an older app build lacks them.
  setLiveWatching?: (watching: boolean) => void
  popOut?: () => void
  // Added so the app's top bar can follow Baihe's own theme: an older app build lacks it.
  setDark?: (dark: boolean) => void
  diagnostics?: () => string
  checkForUpdates?: () => void
  setBarsHidden?: (hidden: boolean) => void
}

const bridge = (): Bridge | null => {
  if (typeof window === 'undefined') return null
  const b = (window as unknown as { BaiheShell?: Bridge }).BaiheShell
  return b && typeof b.get === 'function' && typeof b.set === 'function' ? b : null
}

export const inPhoneShell = (): boolean => bridge() !== null

export function shellSwitch(name: ShellSwitch): boolean {
  try {
    return bridge()?.get(name) ?? false
  } catch {
    return false
  }
}

export function setShellSwitch(name: ShellSwitch, on: boolean): void {
  try {
    bridge()?.set(name, on)
  } catch {
    // The shell went away with the page; nothing to save.
  }
}

/** Tells the app a Live picture is on screen, so leaving the app floats it. Quiet where the app cannot. */
export function setLiveWatching(watching: boolean): void {
  try {
    bridge()?.setLiveWatching?.(watching)
  } catch {
    // The shell went away with the page.
  }
}

/** True in an app build that can float the picture. */
export const canPopOut = (): boolean => typeof bridge()?.popOut === 'function'

export function popOut(): void {
  try {
    bridge()?.popOut?.()
  } catch {
    // The shell went away with the page.
  }
}

/** True in an app build that has a Check for updates button's backing call. */
export const canCheckForUpdates = (): boolean => typeof bridge()?.checkForUpdates === 'function'

export function checkForUpdates(): void {
  try {
    bridge()?.checkForUpdates?.()
  } catch {
    // The shell went away with the page.
  }
}

/** Hides or shows the app's own Studio | Grab strip for a full-screen reader. Quiet where the app cannot. */
export function setShellBarsHidden(hidden: boolean): void {
  try {
    bridge()?.setBarsHidden?.(hidden)
  } catch {
    // The shell went away with the page.
  }
}

/** Tells the app whether Baihe is showing a dark look, so its native top bar matches the page and not only the phone. */
export function setShellDark(dark: boolean): void {
  try {
    bridge()?.setDark?.(dark)
  } catch {
    // The shell went away with the page.
  }
}

/** The phone app's build line and kept crash text for a problem report; null in a browser or an older app build. */
export function shellDiagnostics(): { phone: string; crash: string } | null {
  try {
    const raw = bridge()?.diagnostics?.()
    if (!raw) return null
    const d = JSON.parse(raw) as { phone?: unknown; crash?: unknown }
    return { phone: typeof d.phone === 'string' ? d.phone : '', crash: typeof d.crash === 'string' ? d.crash : '' }
  } catch {
    return null
  }
}
