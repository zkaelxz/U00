/*
 * The Android app's own switches. Baihe's pages run inside its WebView, which hands them `BaiheShell`
 * (android ShellSettings.Bridge); in any browser it is absent and nothing here shows.
 */
export type ShellSwitch = 'alerts_on' | 'clipboard_offer_on'

type Bridge = { get(name: string): boolean; set(name: string, on: boolean): void }

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
