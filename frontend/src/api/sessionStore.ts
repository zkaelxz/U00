// Reading window.sessionStorage can throw (blocked site data), so callers get null instead.
export function browserSession(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}
