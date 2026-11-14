/*
 * Add: the Add box, where a file or a video link becomes English subtitles in one go. A run started
 * from the Library's box shows its progress here, so the title list is not pushed down.
 */
import { useEffect, useState } from 'react'

import { PageShell } from '../components/PageShell'
import { AddBox } from './makeSubtitles/AddBox'
import { clearHandoff, peekHandoff } from './makeSubtitles/addBoxModel'

export default function MakeSubtitlesPage() {
  const [handoff] = useState(() => peekHandoff('run-file'))
  useEffect(() => clearHandoff(handoff), [handoff])
  return (
    <PageShell width="narrow" title="Add" meta="A file or a video link to English subtitles in one go.">
      <AddBox place="page" request={handoff?.request ?? null} />
    </PageShell>
  )
}
