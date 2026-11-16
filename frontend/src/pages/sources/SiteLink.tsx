// SiteLink.tsx -- the small link that opens a source's own website.

import type { SourceSummary } from '../../types/sources'

/** Opens the site itself in a new tab; the demo source has no site, so no link. */
export function SiteLink({ s }: { s: SourceSummary }) {
  if (!s.homepage) return null
  return (
    <a
      className="source-site-link"
      href={s.homepage}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`Open ${s.display_name} website`}
      title={s.homepage}
    >
      {' '}
      ↗
    </a>
  )
}
