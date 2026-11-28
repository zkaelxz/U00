/*
 * The Find & Add page's frame: one heading and four tabs, Add, Discover, Sources and Updates. Each tab keeps its own
 * address (#/make-subtitles, #/discover, #/sources, #/updates), so old links and bookmarks open their tab.
 */
import type { ReactNode } from 'react'

import { featureOn } from '../api/features'
import { ButtonLink } from '../components/Button'
import { PageShell } from '../components/PageShell'
import { useFeatures } from '../hooks/useFeatures'
import { routeHref } from '../router'
import { sourcesHref } from './sources/manageTabs'
import '../components/tabs.css'

type FindAddTab = 'add' | 'discover' | 'sources' | 'updates'

function findAddTabs(sourcesOn: boolean): { id: FindAddTab; label: string; href: string }[] {
  return [
    { id: 'add', label: 'Add', href: routeHref({ name: 'make-subtitles' }) },
    { id: 'discover', label: 'Discover', href: routeHref({ name: 'discover' }) },
    ...(sourcesOn ? [{ id: 'sources' as const, label: 'Sources', href: sourcesHref() }] : []),
    { id: 'updates', label: 'Updates', href: routeHref({ name: 'updates' }) },
  ]
}

export function FindAddShell({ current, className, children }: { current: FindAddTab; className?: string; children: ReactNode }) {
  const sourcesOn = featureOn('sources', useFeatures())
  return (
    <PageShell
      width="wide"
      title="Find & Add"
      label="Find & Add"
      meta="Search by name, or add a video link, a file or a title by hand."
      className={className}
      // The Sources tab carries its own Manage sources tab, so a second link there would be a duplicate.
      actions={sourcesOn && current !== 'sources' ? <ButtonLink href={sourcesHref('manage')}>Manage sources</ButtonLink> : undefined}
      tabs={(
        <nav className="ui-tabs" aria-label="Find & Add">
          {findAddTabs(sourcesOn).map((t) => (
            <a key={t.id} className="ui-tab" href={t.href} aria-current={t.id === current ? 'page' : undefined}>
              {t.label}
            </a>
          ))}
        </nav>
      )}
    >
      {children}
    </PageShell>
  )
}
