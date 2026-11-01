/*
 * Make subtitles: a file to English SRT in one go. It was the first-run view
 * of the Library; it has its own page so the title list is not pushed down.
 */
import { PageShell } from '../components/PageShell'
import { MakeSubtitles } from './makeSubtitles/MakeSubtitles'
import { QuickVideoLink } from './makeSubtitles/QuickVideoLink'

export default function MakeSubtitlesPage() {
  return (
    <PageShell width="narrow" title="Make subtitles" meta="File to English SRT in one go.">
      <QuickVideoLink />
      <MakeSubtitles />
    </PageShell>
  )
}
