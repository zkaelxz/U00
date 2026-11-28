/*
 * Find & Add's Add tab: one box for a name (searched in Sources) or a link, a file, or a title made by hand.
 * A run started from the Library's box shows its progress here, so the title list is not pushed down.
 */
import { useEffect, useState } from 'react'

import { FindAddShell } from './FindAddShell'
import { AddBox } from './makeSubtitles/AddBox'
import { clearHandoff, peekHandoff } from './makeSubtitles/addBoxModel'

export default function MakeSubtitlesPage({ link = null }: { link?: string | null }) {
  const [handoff] = useState(() => peekHandoff('run-file'))
  useEffect(() => clearHandoff(handoff), [handoff])
  return (
    <FindAddShell current="add">
      <AddBox place="page" request={handoff?.request ?? null} initialLink={link} />
    </FindAddShell>
  )
}
