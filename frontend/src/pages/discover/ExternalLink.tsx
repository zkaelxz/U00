import type { ReactNode } from 'react'

import { safeHref } from './discoverFormat'

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
