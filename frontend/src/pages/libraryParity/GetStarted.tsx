import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'

// The first-run view of an empty Library: the way to make subtitles from a
// file, and the ways to find a title instead of bringing one.
export function GetStarted({ canCreate, onDismiss }: { canCreate: boolean; onDismiss: () => void }) {
  return (
    <section className="get-started" aria-label="Get started">
      <p className="muted">No titles yet. Bring an audio or video file, or find one.</p>
      <div className="actions">
        {canCreate && <ButtonLink variant="primary" href={routeHref({ name: 'make-subtitles' })}>Make subtitles</ButtonLink>}
        <ButtonLink variant="secondary" href="#/discover">Discover</ButtonLink>
        <ButtonLink variant="secondary" href="#/sources">Sources</ButtonLink>
        <button type="button" className={buttonClass('ghost')} onClick={onDismiss}>Dismiss</button>
      </div>
    </section>
  )
}
