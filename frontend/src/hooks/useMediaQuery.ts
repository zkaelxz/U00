import { useEffect, useState } from 'react'

// Tracks a CSS media query; false where matchMedia is unavailable (tests, SSR).
export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(query).matches
  const [matches, setMatches] = useState(get)
  useEffect(() => {
    if (!window.matchMedia) return
    const mq = window.matchMedia(query)
    const on = () => setMatches(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return matches
}
