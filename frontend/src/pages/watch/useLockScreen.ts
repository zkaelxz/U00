// The Watch player's lock-screen, notification and headset controls: the phone app's (through phoneShell) and a phone
// browser's own (navigator.mediaSession). The page's audio or video element still does all the playing.
import { useEffect, useRef, type RefObject } from 'react'

import { reportMediaState, type ShellMediaState } from '../../phoneShell'

export const SKIP_SECONDS = 10

export type LockScreenMeta = { title: string; artist: string; artwork?: string }

type ShellWindow = { __baiheMedia?: (action: string, value?: number) => void }

const EVENTS = ['play', 'playing', 'pause', 'ended', 'seeked', 'ratechange', 'durationchange'] as const

const finite = (n: number) => (Number.isFinite(n) ? n : null)

const clampTime = (el: HTMLMediaElement, t: number) =>
  Math.max(0, Number.isFinite(el.duration) ? Math.min(el.duration, t) : t)

/** A press from the lock screen, a notification or a headset, done on the page's own player. */
export function lockScreenAction(el: HTMLMediaElement, action: string, value?: number): void {
  if (action === 'play') {
    // A refused play (no gesture yet) leaves the player paused, which it then reports.
    void Promise.resolve(el.play()).catch(() => undefined)
  } else if (action === 'pause') el.pause()
  else if (action === 'forward' || action === 'back') {
    const by = value && Number.isFinite(value) && value > 0 ? value : SKIP_SECONDS
    el.currentTime = clampTime(el, el.currentTime + (action === 'forward' ? by : -by))
  } else if (action === 'seek' && value !== undefined && Number.isFinite(value)) el.currentTime = clampTime(el, value)
}

const mediaSession = (): MediaSession | null =>
  typeof navigator !== 'undefined' && navigator.mediaSession ? navigator.mediaSession : null

/**
 * Reports the element's state on each change and takes the controls' presses until detached, which reports it
 * stopped so the phone app's notification goes. `refresh` sends the title again after it changes.
 */
export function attachLockScreen(el: HTMLMediaElement, meta: () => LockScreenMeta): { refresh: () => void; detach: () => void } {
  const session = mediaSession()
  const state = (): ShellMediaState['state'] => (el.ended ? 'stopped' : el.paused ? 'paused' : 'playing')

  const report = () => {
    const m = meta()
    const now = state()
    reportMediaState({ state: now, position: el.currentTime, duration: finite(el.duration), rate: el.playbackRate, title: m.title, artist: m.artist })
    if (!session) return
    session.playbackState = now === 'stopped' ? 'none' : now
    const duration = finite(el.duration)
    if (duration && duration > 0 && typeof session.setPositionState === 'function') {
      try {
        session.setPositionState({ duration, playbackRate: el.playbackRate || 1, position: Math.min(el.currentTime, duration) })
      } catch {
        // A browser that refuses the values keeps its own clock.
      }
    }
  }

  const setMetadata = () => {
    if (!session || typeof MediaMetadata !== 'function') return
    const m = meta()
    session.metadata = new MediaMetadata({ title: m.title, artist: m.artist, artwork: m.artwork ? [{ src: m.artwork }] : [] })
  }

  const act = (action: string, value?: number) => lockScreenAction(el, action, value)
  const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
    ['play', () => act('play')],
    ['pause', () => act('pause')],
    ['stop', () => act('pause')],
    ['seekbackward', (d) => act('back', d.seekOffset)],
    ['seekforward', (d) => act('forward', d.seekOffset)],
    ['seekto', (d) => act('seek', d.seekTime)],
  ]
  const setHandlers = (on: boolean) => {
    for (const [name, handler] of handlers) {
      try {
        session?.setActionHandler(name, on ? handler : null)
      } catch {
        // A browser without this action shows its own controls for it.
      }
    }
  }

  const shell = window as unknown as ShellWindow
  shell.__baiheMedia = act
  setMetadata()
  setHandlers(true)
  for (const name of EVENTS) el.addEventListener(name, report)
  if (!el.paused) report()

  return {
    refresh: () => {
      setMetadata()
      report()
    },
    detach: () => {
      for (const name of EVENTS) el.removeEventListener(name, report)
      if (shell.__baiheMedia === act) delete shell.__baiheMedia
      setHandlers(false)
      if (session) {
        session.metadata = null
        session.playbackState = 'none'
      }
      const m = meta()
      reportMediaState({ state: 'stopped', position: el.currentTime, duration: finite(el.duration), rate: el.playbackRate, title: m.title, artist: m.artist })
    },
  }
}

/** Ties the controls to the Watch player while it is on the page; leaving the page ends them. */
export function useLockScreen(mediaRef: RefObject<HTMLMediaElement | null>, id: number, playable: string | null, meta: LockScreenMeta) {
  const metaRef = useRef(meta)
  const attached = useRef<ReturnType<typeof attachLockScreen> | null>(null)
  const { title, artist, artwork } = meta

  useEffect(() => {
    metaRef.current = { title, artist, artwork }
    attached.current?.refresh()
  }, [title, artist, artwork])

  // After the Watch clock's effect, which finds the element for the same id and kind.
  useEffect(() => {
    const el = mediaRef.current
    if (!el || !playable) return
    const a = attachLockScreen(el, () => metaRef.current)
    attached.current = a
    return () => {
      a.detach()
      if (attached.current === a) attached.current = null
    }
  }, [mediaRef, id, playable])
}
