/*
 * Find & Add: one box for a name (searched in Sources) or a link, a file, or a title made by hand.
 * A run started from the Library's box shows its progress here, so the title list is not pushed down.
 */
import { useEffect, useState } from 'react'

import { ButtonLink } from '../components/Button'
import { PageShell } from '../components/PageShell'
import { routeHref } from '../router'
import { AddBox } from './makeSubtitles/AddBox'
import { clearHandoff, peekHandoff } from './makeSubtitles/addBoxModel'
import { sourcesHref } from './sources/manageTabs'

export default function MakeSubtitlesPage({ link = null }: { link?: string | null }) {
  const [handoff] = useState(() => peekHandoff('run-file'))
  useEffect(() => clearHandoff(handoff), [handoff])
  return (
    <PageShell
      width="narrow"
      title="Find & Add"
      meta="Search by name, or add a video link, a file or a title by hand."
      actions={<ButtonLink href={sourcesHref('manage')}>Manage sources</ButtonLink>}
    >
      <AddBox place="page" request={handoff?.request ?? null} initialLink={link} />
      <p className="muted">Looking on official platforms or baihehub? <a href={routeHref({ name: 'discover' })}>Open Discover</a>.</p>
    </PageShell>
  )
}
