import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { routeHref } from '../../router'

// The first-run view of an empty Library: the way to make subtitles from a
// file, and the ways to find a title instead of bringing one.
export function GetStarted({ canCreate, onDismiss }: { canCreate: boolean; onDismiss: () => void }) {
  // Installing is the admin's job; a member would only land on a page that says so.
  const canSetUp = useHolds('admin.diagnostics')
  return (
    <section className="get-started" aria-label="Get started">
      <p className="muted">No titles yet. Bring an audio or video file, or find one.</p>
      <div className="actions">
        {canCreate && <ButtonLink variant="primary" href={routeHref({ name: 'make-subtitles' })}>Make subtitles</ButtonLink>}
        <ButtonLink variant="secondary" href="#/discover">Discover</ButtonLink>
        <ButtonLink variant="secondary" href="#/sources">Sources</ButtonLink>
        {canSetUp && <ButtonLink variant="secondary" href={routeHref({ name: 'diagnostics', card: 'recommended' })}>Recommended installs</ButtonLink>}
        <button type="button" className={buttonClass('ghost')} onClick={onDismiss}>Dismiss</button>
      </div>
    </section>
  )
}
