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
  // Added so the sepia look reaches the app's top bar too; an older build only has setDark.
  setLook?: (look: string) => void
  diagnostics?: () => string
  checkForUpdates?: () => void
  setBarsHidden?: (hidden: boolean) => void
  saveFile?: (key: string, name: string, mime: string, base64: string) => boolean
  changeAddress?: (key: string) => void
  notificationsBlocked?: () => boolean
  // Added with the lock-screen controls: an older app build lacks it.
  mediaState?: (key: string, json: string) => void
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

// Set by the app on Baihe's own top page only, so a frame inside it cannot call the calls that need it.
const pageKey = (): unknown => (window as unknown as { __baiheSaveKey?: unknown }).__baiheSaveKey

/** True in an app build that can change its Baihe address from here. */
export const canChangeAddress = (): boolean => typeof bridge()?.changeAddress === 'function' && typeof pageKey() === 'string'

/** Opens the app's own Baihe address dialog. */
export function changeShellAddress(): void {
  try {
    const key = pageKey()
    if (typeof key === 'string') bridge()?.changeAddress?.(key)
  } catch {
    // The shell went away with the page.
  }
}

// The app's MAX_FILE_BYTES (SaveDownload.java); a bigger file is left to the browser path, where the app says why.
const SHELL_SAVE_MAX_BYTES = 30 * 1024 * 1024

/**
 * Hands a file the page built to the app, which saves it in Downloads: the app's WebView cannot follow a
 * blob: link. True when the app took it (it then says itself whether the save worked).
 */
export function saveInShell(blob: Blob, filename: string): boolean {
  const b = bridge()
  if (typeof b?.saveFile !== 'function' || blob.size > SHELL_SAVE_MAX_BYTES) return false
  if (typeof pageKey() !== 'string') return false
  void blob.arrayBuffer().then((buffer) => {
    try {
      // Read again: the app hands out a new key on each page load.
      b.saveFile?.(String(pageKey()), filename, blob.type, toBase64(new Uint8Array(buffer)))
    } catch {
      // The shell went away with the page.
    }
  })
  return true
}

function toBase64(bytes: Uint8Array): string {
  let text = ''
  // In slices: String.fromCharCode with one argument per byte overflows the call stack on a large file.
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(text)
}

/** True when Android blocks the app's notifications; false in a browser or an older app build, which can't tell. */
export function shellNotificationsBlocked(): boolean {
  try {
    return bridge()?.notificationsBlocked?.() ?? false
  } catch {
    return false
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

/** Tells the app which look Baihe is showing, so its native top bar matches the page and not only the phone. */
export function setShellLook(look: 'light' | 'dark' | 'sepia' | 'oled'): void {
  try {
    const b = bridge()
    if (b?.setLook) b.setLook(look)
    else b?.setDark?.(look === 'dark' || look === 'oled')
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

/** What the Watch page's player is doing, for the phone app's lock-screen controls. Seconds; null while unknown. */
export type ShellMediaState = {
  state: 'playing' | 'paused' | 'stopped'
  position: number
  duration: number | null
  rate: number
  title: string
  artist: string
}

/** Fired by the app on the top page right after it sets the page key. */
export const SHELL_KEY_EVENT = 'baihekey'
let pendingMedia: string | null = null

/** Tells the app what the player is doing; the time it was read goes with it, so the lock screen's clock can run on from there. */
export function reportMediaState(s: ShellMediaState): void {
  try {
    if (typeof bridge()?.mediaState !== 'function') return
    const json = JSON.stringify({ ...s, at: Date.now() })
    const key = pageKey()
    if (typeof key === 'string') {
      bridge()?.mediaState?.(key, json)
      return
    }
    // Play pressed before the app handed this page its key: send the latest state once the key arrives.
    if (!pendingMedia && typeof window.addEventListener === 'function') window.addEventListener(SHELL_KEY_EVENT, sendPendingMedia, { once: true })
    pendingMedia = json
  } catch {
    // The shell went away with the page.
  }
}

function sendPendingMedia(): void {
  const json = pendingMedia
  pendingMedia = null
  const key = pageKey()
  try {
    if (json && typeof key === 'string') bridge()?.mediaState?.(key, json)
  } catch {
    // The shell went away with the page.
  }
}
