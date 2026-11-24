// Pure helpers for the page menu a long press opens: the press timing and the saved picture's name.

export const LONG_PRESS_MS = 500
// A finger that travels further than this is scrolling or panning, not holding.
export const LONG_PRESS_MOVE_PX = 10

type Pt = { x: number; y: number }

/** One press at a time; a press's `fire` runs once if the pointer stays put for LONG_PRESS_MS. */
export function longPress(setTimer: typeof setTimeout = setTimeout, clearTimer: typeof clearTimeout = clearTimeout) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let down: Pt | null = null
  const cancel = () => {
    clearTimer(timer)
    timer = undefined
    down = null
  }
  return {
    down(p: Pt, pointers: number, fire: () => void) {
      cancel()
      // A second finger means a pinch.
      if (pointers > 1) return
      down = p
      timer = setTimer(() => {
        down = null
        fire()
      }, LONG_PRESS_MS)
    },
    move(p: Pt) {
      if (down && Math.hypot(p.x - down.x, p.y - down.y) > LONG_PRESS_MOVE_PX) cancel()
    },
    cancel,
  }
}

const EXTENSIONS: Record<string, string> = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/avif': 'avif' }

/** "Night Rain - page 012.jpg": sorts in page order and is safe on Windows, macOS and Android. */
export function pageFileName(title: string, page: number, mime: string): string {
  const safe = title.replace(/[\\/:*?"<>|]|\p{Cc}/gu, ' ').replace(/\s+/g, ' ').trim().slice(0, 80)
  const ext = EXTENSIONS[mime.split(';')[0].trim().toLowerCase()] ?? 'jpg'
  return `${safe || 'Comic'} - page ${String(page).padStart(3, '0')}.${ext}`
}

// The cover route takes these three; another type would be refused after the upload.
export const COVER_TYPES = ['image/jpeg', 'image/png', 'image/webp']
