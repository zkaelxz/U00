import { useEffect, useRef } from 'react'

// Keeps the next pages' images warm in the browser cache.
export function usePreloadImages(urls: string[]) {
  const held = useRef(new Map<string, HTMLImageElement>())
  const key = urls.join('\n')
  useEffect(() => {
    const want = key ? key.split('\n') : []
    for (const u of want) {
      if (held.current.has(u)) continue
      const img = new Image()
      img.decoding = 'async'
      img.src = u
      held.current.set(u, img)
    }
    // Only the current window is held; older ones can be collected.
    for (const u of [...held.current.keys()]) if (!want.includes(u)) held.current.delete(u)
  }, [key])
}
