/*
 * Make subtitles: a file to English SRT in one go. It was the first-run view
 * of the Library; it has its own page so the title list is not pushed down.
 */
import { MakeSubtitles } from './makeSubtitles/MakeSubtitles'

export default function MakeSubtitlesPage() {
  return (
    <div className="page-narrow">
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">Make subtitles</h2>
          <p className="page-meta">File to English SRT in one go.</p>
        </div>
      </header>
      <MakeSubtitles />
    </div>
  )
}
