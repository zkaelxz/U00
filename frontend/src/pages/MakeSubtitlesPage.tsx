/*
 * Find & Add's Add tab: one box for a name (searched in Sources) or a link, a file, or a title made by hand.
 */
import { FindAddShell } from './FindAddShell'
import { AddBox } from './makeSubtitles/AddBox'

export default function MakeSubtitlesPage({ link = null }: { link?: string | null }) {
  return (
    <FindAddShell current="add">
      <AddBox initialLink={link} />
    </FindAddShell>
  )
}
