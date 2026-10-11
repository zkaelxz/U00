import type { MouseEvent, ReactNode } from 'react'

import { getSourceLink } from '../../api/library'
import { usePcOnly } from '../../hooks/usePcOnly'
import { safeHref } from './discoverFormat'

/**
 * "Open original page": a link to the address an import came from, or nothing unless it is http(s).
 * `href` is the cleaned link every viewer gets. With `dramaId`, a click on the main PC opens the link
 * as imported instead (its query can hold what the page needs, such as a video id), fetched only then
 * from the PC-only source-link route so it never sits in page state.
 */
export function SourceLink({ href, dramaId, children = 'Open original page', className, 'aria-label': ariaLabel }: {
  href: string | null | undefined; dramaId?: number; children?: ReactNode; className?: string; 'aria-label'?: string
}) {
  const pc = usePcOnly()
  const safe = safeHref(href)
  if (!safe) return null
  const onClick =
    dramaId != null && pc === 'local'
      ? (e: MouseEvent<HTMLAnchorElement>) => {
          e.preventDefault()
          void openFullSourceLink(dramaId, safe)
        }
      : undefined
  return (
    <a href={safe} className={className ?? 'source-link'} target="_blank" rel="noopener noreferrer" aria-label={ariaLabel} onClick={onClick}>
      {children}
    </a>
  )
}

async function openFullSourceLink(dramaId: number, fallback: string): Promise<void> {
  // The tab opens inside the click so a popup blocker allows it; the address follows the fetch.
  const tab = window.open('', '_blank')
  if (tab) tab.opener = null
  let url = fallback
  try {
    url = safeHref((await getSourceLink(dramaId)).url) ?? fallback
  } catch {
    // The cleaned link still gets the person to the site.
  }
  if (tab) tab.location.href = url
  else window.open(url, '_blank', 'noopener,noreferrer')
}

/** A link to another site, opened in a new tab; a non-http(s) address shows as plain text. */
export function ExternalLink({ href, children, className }: { href: string | null | undefined; children: ReactNode; className?: string }) {
  const safe = safeHref(href)
  return safe ? (
    <a href={safe} className={className} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <span className={className}>{children}</span>
  )
}
