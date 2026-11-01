/*
 * The installed app's "Share to Baihe" entry (manifest share_target, GET /share). Android Chrome puts a
 * shared page's address in `url` or, more often, inside `text` ("Title https://site/..."). It is handed
 * to the Sources paste-a-link box; nothing is fetched or imported until the user presses Preview there.
 */
export const MAX_SHARED_LINK_LEN = 2000

export function isSharedLink(v: string): boolean {
  if (!v || v.length > MAX_SHARED_LINK_LEN) return false
  try {
    const u = new URL(v)
    return (u.protocol === 'http:' || u.protocol === 'https:') && !u.username && !u.password
  } catch {
    return false
  }
}

export function linkFromShare(params: URLSearchParams): string | null {
  const direct = (params.get('url') ?? '').trim()
  if (isSharedLink(direct)) return direct
  const inText = (params.get('text') ?? '').match(/https?:\/\/\S+/)?.[0] ?? ''
  return isSharedLink(inText) ? inText : null
}

/** The hash to continue at when the page was opened at /share, or null for any other address. */
export function shareRedirect(pathname: string, search: string): string | null {
  if (pathname !== '/share') return null
  const link = linkFromShare(new URLSearchParams(search))
  return link ? `#/sources?url=${encodeURIComponent(link)}` : '#/sources'
}
