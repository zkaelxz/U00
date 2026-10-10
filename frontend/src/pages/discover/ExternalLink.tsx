import type { ReactNode } from 'react'

import { safeHref } from './discoverFormat'

/** "Open original page": a link to the address an import came from, or nothing unless it is http(s). */
export function SourceLink({ href, children = 'Open original page', className, 'aria-label': ariaLabel }: {
  href: string | null | undefined; children?: ReactNode; className?: string; 'aria-label'?: string
}) {
  const safe = safeHref(href)
  return safe ? (
    <a href={safe} className={className ?? 'source-link'} target="_blank" rel="noopener noreferrer" aria-label={ariaLabel}>
      {children}
    </a>
  ) : null
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
