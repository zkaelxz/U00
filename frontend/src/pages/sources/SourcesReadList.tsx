// SourcesReadList.tsx -- the sources list for a device away from the PC: status and details, no switches.

import { useState } from 'react'

import { buttonClass } from '../../components/uiClasses'
import type { SourceSummary } from '../../types/sources'
import { SiteState } from './SitesTab'
import { SourceDetail } from './SourceDetail'

type Props = { sources: SourceSummary[]; onSearchOnly: (name: string) => void }

/** Editing a site (on/off, pace, sign-in, tests) is PC only, so this shows the same rows with Details read-only. */
export function SourcesReadList({ sources, onSearchOnly }: Props) {
  const [open, setOpen] = useState<string | null>(null)
  if (sources.length === 0) return <p className="muted">No sources are set up yet.</p>
  return (
    <ul className="source-cards" aria-label="Sources">
      {sources.map((s) => (
        <li key={s.name}>
          <div className="source-card-line">
            <span>
              <strong>{s.display_name}</strong>
              {!s.enabled && <small className="muted"> Off</small>}
            </span>
            <SiteState light={s.health} extensionOnly={s.extension_only} />
          </div>
          <div className="source-card-actions">
            <button
              type="button"
              className={buttonClass('primary', 'sm')}
              aria-label={`Search ${s.display_name}`}
              disabled={!(s.enabled && s.supports.search)}
              onClick={() => onSearchOnly(s.name)}
            >
              Search
            </button>
            <button
              type="button"
              className={buttonClass('secondary', 'sm')}
              aria-expanded={open === s.name}
              aria-label={`Details: ${s.display_name}`}
              onClick={() => setOpen(open === s.name ? null : s.name)}
            >
              Details
            </button>
            {s.homepage ? (
              <a
                className={buttonClass('secondary', 'sm')}
                href={s.homepage}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open ${s.display_name} website`}
              >
                Open site ↗
              </a>
            ) : <span aria-hidden="true" />}
          </div>
          {open === s.name && (
            <div className="source-detail-wrap">
              <SourceDetail name={s.name} readOnly />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
