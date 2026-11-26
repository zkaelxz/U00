/** TV mode of a watched Live session: full screen where the browser allows it, remembered per device, left by Esc, the remote's back or a tap on Exit. */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { usePersistedState } from '../../hooks/usePersistedState'

export type TvVideo = 'side' | 'above' | 'off'

type FsDoc = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> }
type FsEl = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> }

const fullscreenOn = (doc: FsDoc) => doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null
const BAR_SECONDS = 6
// TV remotes send these where a keyboard sends Escape.
const BACK_KEYS = ['Escape', 'GoBack', 'BrowserBack']

/** `pinned`: keep the bar up (a remembered TV mode nobody has touched yet, or a session that has ended). */
export function useTvMode(root: RefObject<HTMLElement | null>, pinned = false) {
  const [on, setOn] = usePersistedState<boolean>('live.tv', false)
  const [video, setVideo] = usePersistedState<TvVideo>('live.tvVideo', 'side')
  const [barShown, setBarShown] = useState(true)
  // Whether this page has been in full screen, so that leaving it (Esc) also leaves TV mode and a browser without the API does not.
  const wasFullscreen = useRef(false)
  // The full-screen request resolves later; by then Exit may already have been pressed.
  const onRef = useRef(on)
  onRef.current = on
  const exiting = useRef(false)
  const [touched, setTouched] = useState(false)
  const barRef = useRef(true)

  const leaveFullscreen = useCallback(() => {
    const doc = document as FsDoc
    wasFullscreen.current = false
    if (fullscreenOn(doc)) void (doc.exitFullscreen ?? doc.webkitExitFullscreen)?.call(doc)?.catch?.(() => {})
  }, [])

  const requestFullscreen = useCallback(async () => {
    const el = root.current as FsEl | null
    const doc = document as FsDoc
    if (!el || fullscreenOn(doc)) return
    try {
      await (el.requestFullscreen ?? el.webkitRequestFullscreen)?.call(el)
      wasFullscreen.current = true
      if (!onRef.current) leaveFullscreen()
    } catch {
      // Refused (no gesture, or a browser without the API): the fixed layout still fills the window.
    }
  }, [root, leaveFullscreen])

  const exit = useCallback(() => {
    // history.back() is asynchronous: a double press must not go back twice and leave the page.
    if (exiting.current) return
    exiting.current = true
    setOn(false)
    leaveFullscreen()
    // Take back the history entry pushed on entry, so the remote's back is not spent on it later.
    if (history.state?.liveTv) history.back()
  }, [setOn, leaveFullscreen])

  const enter = useCallback(() => {
    exiting.current = false
    setOn(true)
    setTouched(true)
    setBarShown(true)
    void requestFullscreen()
  }, [setOn, requestFullscreen])

  useEffect(() => {
    if (!on) return
    // One entry however often the mode is re-entered (reload, switching session), or Back is spent on dead ones.
    if (!history.state?.liveTv) history.pushState({ ...history.state, liveTv: true }, '')
    const doc = document as FsDoc
    const onPop = () => {
      setOn(false)
      leaveFullscreen()
    }
    const onFullscreenChange = () => {
      if (wasFullscreen.current && !fullscreenOn(doc)) {
        wasFullscreen.current = false
        setOn(false)
        if (history.state?.liveTv) history.back()
      }
    }
    window.addEventListener('popstate', onPop)
    document.addEventListener('fullscreenchange', onFullscreenChange)
    document.addEventListener('webkitfullscreenchange', onFullscreenChange)
    return () => {
      window.removeEventListener('popstate', onPop)
      document.removeEventListener('fullscreenchange', onFullscreenChange)
      document.removeEventListener('webkitfullscreenchange', onFullscreenChange)
      // Leaving the page (not just TV mode) must not leave the browser in full screen.
      leaveFullscreen()
    }
  }, [on, setOn, leaveFullscreen])

  // A remembered TV mode comes back without a gesture, which browsers need before full screen: the first tap or key press goes full screen.
  useEffect(() => {
    if (!on || fullscreenOn(document as FsDoc)) return
    // A click or key press counts as a gesture; the start of a touch does not.
    const once = (e: Event) => {
      if (e instanceof KeyboardEvent && BACK_KEYS.includes(e.key)) return
      if ((e.target as Element | null)?.closest?.('.live-tv-bar')) return
      void requestFullscreen()
    }
    window.addEventListener('click', once, { once: true })
    window.addEventListener('keydown', once, { once: true })
    return () => {
      window.removeEventListener('click', once)
      window.removeEventListener('keydown', once)
    }
  }, [on, requestFullscreen])

  // The bar with Exit shows on any touch, pointer move or key and hides again, so the picture and lines have the whole screen.
  useEffect(() => {
    if (!on) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const wake = (e?: Event) => {
      if (e) setTouched(true)
      setBarShown(true)
      clearTimeout(timer)
      if (!pinned) timer = setTimeout(() => setBarShown(false), BAR_SECONDS * 1000)
    }
    const onKey = (e: KeyboardEvent) => {
      if (BACK_KEYS.includes(e.key)) {
        e.preventDefault()
        exit()
        return
      }
      // A press while the bar is hidden only brings it back; it must not also press the Exit it focuses.
      if (!barRef.current && (e.key === 'Enter' || e.key === ' ')) e.preventDefault()
      wake(e)
    }
    wake()
    window.addEventListener('pointerdown', wake)
    window.addEventListener('pointermove', wake)
    window.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('pointerdown', wake)
      window.removeEventListener('pointermove', wake)
      window.removeEventListener('keydown', onKey)
    }
  }, [on, exit, pinned])

  const shown = barShown || pinned || !touched

  barRef.current = shown

  return { on, enter, exit, video, setVideo, barShown: shown, showBar: () => setBarShown(true) }
}
