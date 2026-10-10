import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { MakeSubtitles } from '../makeSubtitles/MakeSubtitles'

// The first-run view of an empty Library: the Make subtitles card, with the
// ways to find a title instead of bringing a file.
export function GetStarted({ onDismiss }: { onDismiss: () => void }) {
  return (
    <section className="get-started" aria-label="Get started">
      <MakeSubtitles />
      <div className="actions">
        <ButtonLink variant="secondary" href="#/discover">Discover</ButtonLink>
        <ButtonLink variant="secondary" href="#/sources">Sources</ButtonLink>
        <button type="button" className={buttonClass('ghost')} onClick={onDismiss}>Dismiss</button>
      </div>
    </section>
  )
}
