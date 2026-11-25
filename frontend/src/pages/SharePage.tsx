/*
 * Share picker: what to do with a link another app shared to Baihe. Nothing is fetched or started
 * until one of the choices is pressed, so a mistaken share costs nothing.
 */
import { ButtonLink } from '../components/Button'
import { PageShell } from '../components/PageShell'
import { canOpenGrab } from '../components/appVersionText'
import { useHolds } from '../hooks/useHolds'
import { routeHref } from '../router'

const hostOf = (link: string): string => {
  try {
    return new URL(link).hostname
  } catch {
    return ''
  }
}

export default function SharePage({ url }: { url: string | null }) {
  // Subtitling a video and starting Live both have the PC fetch the address: the Import from a link right.
  const mayFetch = useHolds('media.import_url')
  const inPhoneApp = typeof navigator !== 'undefined' && canOpenGrab(navigator.userAgent)
  if (!url) {
    return (
      <PageShell width="narrow" title="Share to Baihe" meta="No web link was found in what was shared.">
        <ButtonLink href={routeHref({ name: 'sources' })}>Open Sources</ButtonLink>
      </PageShell>
    )
  }
  return (
    <PageShell width="narrow" title="Share to Baihe" meta={`What should Baihe do with this link? ${hostOf(url)}`}>
      <div className="stack">
        {mayFetch && (
          <ButtonLink variant="primary" href={routeHref({ name: 'make-subtitles', url })}>Subtitle this video</ButtonLink>
        )}
        {mayFetch && <ButtonLink href={routeHref({ name: 'live', url })}>Start Live on it</ButtonLink>}
        {inPhoneApp && (
          // Quick read runs in Baihe Grab's own browser, so only the phone app can open it.
          <ButtonLink href={`baihegrab://quickread?url=${encodeURIComponent(url)}`}>Quick read this page</ButtonLink>
        )}
        <ButtonLink variant="ghost" href={routeHref({ name: 'sources', url })}>Find it in Sources</ButtonLink>
      </div>
    </PageShell>
  )
}
