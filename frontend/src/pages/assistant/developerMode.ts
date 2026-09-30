/*
 * Developer Mode: whether the "Assistant" nav link shows. The setting lives
 * on the server (GET /api/assistant/settings, PC only). The link is hidden
 * when the mode is off, on another device (the /api/meta check says remote,
 * or the route answers 403) and on any error.
 *
 * Whoever flips the mode calls announceDeveloperMode() after a successful
 * save; useDeveloperMode() listens and asks the server again.
 */
import { useEffect, useState } from 'react'

import { getAssistantSettings } from '../../api/assistant'
import { getPcMode, loadPcMode, type PcMode } from '../../api/pcOnly'
import type { AssistantSettings } from '../../types/assistant'

export const DEVELOPER_MODE_EVENT = 'baihe:developer-mode'

export function announceDeveloperMode(on: boolean): void {
  window.dispatchEvent(new CustomEvent(DEVELOPER_MODE_EVENT, { detail: { on } }))
}

export interface DeveloperModeDeps {
  waitForPcMode: () => Promise<void>
  pcMode: () => PcMode
  getSettings: () => Promise<Pick<AssistantSettings, 'developer_mode'>>
}

const DEFAULT_DEPS: DeveloperModeDeps = {
  waitForPcMode: () => loadPcMode(),
  pcMode: getPcMode,
  getSettings: () => getAssistantSettings(),
}

/** true only when the server says developer_mode is on; false when remote or on any error. */
export async function loadDeveloperMode(deps: DeveloperModeDeps = DEFAULT_DEPS): Promise<boolean> {
  try {
    // Wait for /api/meta so another device makes no call here.
    await deps.waitForPcMode()
    if (deps.pcMode() === 'remote') return false
    const s = await deps.getSettings()
    return s?.developer_mode === true
  } catch {
    return false
  }
}

/**
 * For the nav: loads once the app is shown (`enabled`; not behind the sign-in
 * gate) and again on each announceDeveloperMode().
 */
export function useDeveloperMode(enabled = true): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (!enabled) return
    let live = true
    let seq = 0
    const refresh = () => {
      const mine = ++seq
      void loadDeveloperMode().then((v) => {
        if (live && mine === seq) setOn(v)
      })
    }
    refresh()
    window.addEventListener(DEVELOPER_MODE_EVENT, refresh)
    return () => {
      live = false
      window.removeEventListener(DEVELOPER_MODE_EVENT, refresh)
    }
  }, [enabled])
  return enabled && on
}
