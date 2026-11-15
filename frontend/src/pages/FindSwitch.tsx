/*
 * The Discover | Sources switch at the top of both pages, which the left rail lists as one entry, Find.
 */
import { ButtonLink } from '../components/Button'
import { featureOn } from '../api/features'
import { useFeatures } from '../hooks/useFeatures'
import { routeHref } from '../router'

export function FindSwitch({ current }: { current: 'discover' | 'sources' }) {
  const features = useFeatures()
  // With Sources switched off there is only Discover, so a one-link switch would be noise.
  if (!featureOn('sources', features)) return null
  return (
    <nav className="find-switch" aria-label="Find" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
      {([['discover', 'Discover'], ['sources', 'Sources']] as const).map(([name, label]) => (
        <ButtonLink key={name} size="sm" variant={name === current ? 'primary' : 'ghost'} href={routeHref({ name })} aria-current={name === current ? 'page' : undefined}>
          {label}
        </ButtonLink>
      ))}
    </nav>
  )
}
