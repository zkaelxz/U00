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
  checkForUpdates?: () => void
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
