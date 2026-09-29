import type { ReactNode } from 'react'

import { safeHref } from './discoverFormat'

/** A link to another site, opened in a new tab; a non-http(s) address shows as plain text. */
export function ExternalLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  const safe = safeHref(href)
  return safe ? (
    <a href={safe} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <span>{children}</span>
  )
}
